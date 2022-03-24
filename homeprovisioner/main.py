from rich.traceback import install as traceback_install
from rich.logging import RichHandler
import logging, sys, threading
from time import sleep
import click
import requests
import toml
from zeroconf import ServiceBrowser, Zeroconf

FORMAT = "%(message)s"
if sys.stdin.isatty():
    traceback_install(show_locals=True)
    logging.basicConfig(level=logging.INFO, handlers=[RichHandler()])
else:
    logging.basicConfig(level=logging.INFO)

LOG = logging.getLogger("homeprovisioner")
# LOG.setLevel(logging.DEBUG)


class ShellyZeroconfListener:
    def __init__(self, configuration_file):
        self.configuration_file = configuration_file
        self.config = toml.load(configuration_file)
        self.known_devices = {}

    def ota_update(self, usable_name, ip):
        reboot_flag = False
        try:
            ota_status = requests.get(f"http://{ip}/ota").json()
        except requests.exceptions.ConnectionError as e:
            LOG.error(
                f"Unable to check for updates for {usable_name} (http://{ip}): {e}"
            )
            return
        if ota_status["has_update"]:
            LOG.info(
                f"Update available for {usable_name} "
                f"{self.config['shellies'][usable_name]['id']} "
                f"(http://{ip})"
            )
            requests.post(f"http://{ip}/ota", data={"update": True})
            reboot_flag = True
            while (
                ota_check := requests.get(f"http://{ip}/ota").json()["status"]
            ) != "idle":
                LOG.debug(f"{usable_name} OTA status: {ota_check}")
                sleep(1)
        else:
            LOG.info(
                f"No updates available for {usable_name} "
                f"{self.config['shellies'][usable_name]['id']} "
                f"(http://{ip})"
            )

        if reboot_flag:
            LOG.info(
                f"Update done. Rebooting {usable_name} "
                f"{self.config['shellies'][usable_name]['id']} "
                f"(http://{ip})"
            )
            try:
                requests.get(f"http://{ip}/reboot")
            except requests.exceptions.ConnectionError as e:
                LOG.critical(
                    f"Device {usable_name} "
                    f"{self.config['shellies'][usable_name]['id']} "
                    f"(http://{ip}) was updated but couldn't be rebooted."
                )
                return

    def check_all_for_updates(self):
        self.config = toml.load(self.configuration_file)
        for usable_name, ip in self.known_devices.items():
            self.ota_update(usable_name, ip)

    def push_settings(self, usable_name, ip):
        if usable_name not in self.config["shellies"].keys():
            LOG.error(f"Found an unconfigured device: {usable_name}")
            return

        reboot_flag = False

        # The settings management is a bit convoluted, because the JSON layout
        # returned by the Shelly "settings" endpoint is different from the JSON layout
        # that needs to be POSTed for changing/updating settings (sigh).
        # Therefore, I first fetch the settings to "device_settings", then I create
        # a new dictionary called "current_settings" that has the same layout as
        # the layout used for submission and populate the relevant/supported values
        # from "device_settings" to it, i.e. at this point I have a dictionary with the
        # current device settings that matches the layout used for changing/updating
        # settings.
        # Finally, I check if "current_settings" is different from "new_settings" and
        # if so, "new_settings" is POSTed and followed by a reboot.
        device_settings = requests.get(f"http://{ip}/settings").json()
        LOG.debug(device_settings)
        current_settings = {}
        current_settings["mqtt_enable"] = device_settings["mqtt"]["enable"]
        current_settings["mqtt_server"] = device_settings["mqtt"]["server"]
        current_settings["mqtt_clean_session"] = device_settings["mqtt"][
            "clean_session"
        ]
        current_settings["mqtt_retain"] = device_settings["mqtt"]["retain"]
        current_settings["mqtt_user"] = device_settings["mqtt"]["user"]
        try:
            current_settings["mqtt_id"] = device_settings["mqtt"]["id"]
        except KeyError:
            pass
        current_settings["mqtt_max_qos"] = device_settings["mqtt"]["max_qos"]
        current_settings["name"] = device_settings["name"]
        new_settings = {
            "mqtt_enable": True,
            "mqtt_server": f"{self.config['mqtt_host']}:{self.config['mqtt_port']}",
            "mqtt_clean_session": True,
            "mqtt_retain": True,
            "mqtt_id": self.config["shellies"][usable_name]["id"],
            "mqtt_max_qos": 1,
            "name": self.config["shellies"][usable_name]["id"].replace("/", "_"),
        }
        try:
            new_settings["mqtt_user"] = self.config["mqtt_username"]
        except KeyError:
            new_settings["mqtt_user"] = ""

        if "relays" in device_settings.keys():
            current_settings["relays"] = []
            new_settings["relays"] = []
            for relay in device_settings["relays"]:
                current_relay_settings = {}
                new_relay_settings = {}
                if "default_state" in relay.keys():
                    current_relay_settings["default_state"] = relay["default_state"]
                    new_relay_settings["default_state"] = "last"
                if "btn_type" in relay.keys():
                    current_relay_settings["btn_type"] = relay["btn_type"]
                    new_relay_settings["btn_type"] = (
                        "momentary"
                        if "btn_type" not in self.config["shellies"][usable_name].keys()
                        else self.config["shellies"][usable_name]["btn_type"]
                    )
                current_settings["relays"].append(current_relay_settings)
                new_settings["relays"].append(new_relay_settings)

        if current_settings != new_settings:
            LOG.info(
                f"Pushing configuration to {usable_name} "
                f"{self.config['shellies'][usable_name]['id']} "
                f"(http://{ip})"
            )
            LOG.debug(new_settings)
            # The password is a special case because the current password is never
            # sent to us when querying the current settings.
            try:
                new_settings["mqtt_pass"] = self.config["mqtt_password"]
            except KeyError:
                new_settings["mqtt_pass"] = ""

            try:
                update_request = requests.post(
                    f"http://{ip}/settings", data=new_settings
                )
            except Exception:
                LOG.error(
                    f"Unable to push new settings to {usable_name} "
                    f"{self.config['shellies'][usable_name]['id']} "
                    f"(http://{ip})"
                )
                return
            try:
                for i, relay in enumerate(new_settings["relays"]):
                    new_relay_settings = {}
                    for k in relay.keys():
                        new_relay_settings[k] = relay[k]
                    requests.post(
                        f"http://{ip}/settings/relay/{i}",
                        data=new_relay_settings,
                    )
            except KeyError:
                pass
            if update_request.status_code == 200:
                reboot_flag = True
        if reboot_flag:
            LOG.info(
                f"Update done. Rebooting {usable_name} "
                f"{self.config['shellies'][usable_name]['id']} "
                f"(http://{ip})"
            )
            requests.get(f"http://{ip}/reboot")
        self.ota_update(usable_name, ip)

    def manage_service(self, zeroconf, service_type, name, action):
        if not name.startswith("shelly"):
            return
        info = zeroconf.get_service_info(service_type, name)
        try:
            usable_name = info.properties[b"id"].decode("utf-8")
        except KeyError:
            usable_name = info.name.split(".")[0]
        ip = info.parsed_addresses()[0]
        usable_name = usable_name.strip()
        LOG.info(
            f"Zeroconf {action}-service from: {usable_name} "
            f"{self.config['shellies'][usable_name]['id']} "
            f"(http://{ip})"
        )
        self.known_devices[usable_name] = ip
        self.config = toml.load(self.configuration_file)
        threading.Thread(target=self.push_settings, args=(usable_name, ip)).start()

    def add_service(self, zeroconf, service_type, name):
        self.manage_service(zeroconf, service_type, name, "add")

    def update_service(self, zeroconf, service_type, name):
        self.manage_service(zeroconf, service_type, name, "update")

    def remove_service(self, zeroconf, service_type, name):
        pass


@click.command()
@click.option(
    "-c",
    "--config",
    "configuration_file",
    default="/etc/homeprovisioner/deviceconfig.toml",
    show_default=True,
    type=click.Path(file_okay=True, dir_okay=False, readable=True),
)
def main(configuration_file):
    zeroconf = Zeroconf()
    shelly_zeroconf_listener = ShellyZeroconfListener(configuration_file)
    browser = ServiceBrowser(zeroconf, "_http._tcp.local.", shelly_zeroconf_listener)

    try:
        while True:
            sleep(3600 * 24)  # 24h
            shelly_zeroconf_listener.check_all_for_updates()
    finally:
        zeroconf.close()


if __name__ == "__main__":
    main()
