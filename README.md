# homeprovisioner

This is a service that takes care of provisioning devices, currently only Shelly devices,
for my home-automation setup. It reads configuration from a TOML file and applies
it to the Shellies it discovers (via Zeroconf/Bonjour).

Furthermore, it will check if the devices it knows of have a firmware-update available
every 24 hours.

## Installation

I installed this in `/opt` but this can be anywhere. Just update the homeprovisioner.service file to match.

### Install Poetry
```bash
pip3 install poetry
```

```bash
cd /opt
git clone http://github.com/johannfr/homeprovisioner
cd homeprovisioner
sudo -u homeprovisioner poetry install --no-dev
```

Then copy the homeprovisioner.service file to /lib/systemd/system (or another systemd directory of your liking), update necessary environment variables, enable it and run it.

```bash
cp homeprovisioner.service /lib/systemd/system
# Edit /lib/systemd/system/homeprovisioner.service
systemctl daemon-reload
systemctl enable homeprovisioner
systemctl start homeprovisioner
```
