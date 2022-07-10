import logging
import json
import os
import requests
import sys
import time
from prometheus_client import start_http_server, Counter, Summary, Gauge

# Get ENV
LOGLEVEL = os.environ.get('LOGLEVEL', 'INFO').upper()
#THERA_WEBHOOK = os.environ.get('THERA_WEBHOOK')
THERA_WEBHOOK = 'https://discord.com/api/webhooks/662168265182937099/5U35P0ECLD4cEXoe5W0Ugcl9F70P3nxlKttlMLe7fR0RYhfoPPVi2XWd05Db3DxL8cf7'
THERA_MAXDISTANCE = os.environ.get('THERA_MAXDISTANCE', '5')

ESI_CALL = Counter('esi_calls', 'Call to ESI API server')
DISCORD_CALL = Counter('discord_calls', 'Call to ESI API server')
SCOUT_DATA = Gauge('scout_data', 'Total of data at scout API')

logging.basicConfig(level=LOGLEVEL,
                    format=json.dumps({'time': '%(asctime)s', 'level': '%(levelname)s', 'message': '%(message)s'}))

system_names = ["Jita", "5ZXX-K"]
system_ids = []

lastscoutid = 0
newlastscoutid = 0

logging.info("Init checks")

logging.debug("Checking THERA_WEBHOOK")
if THERA_WEBHOOK is None:
    logging.error("No webhook url set")
    sys.exit()
else:
    logging.debug("THERA_WEBHOOK is ok")

logging.debug("Checking THERA_MAXDISTANCE")
try:
    THERA_MAXDISTANCE = int(THERA_MAXDISTANCE)
except ValueError:
    logging.error("Failed to parse THERA_MAXDISTANCE")
    sys.exit()
else:
    logging.debug("THERA_MAXDISTANCE is ok")

logging.info("Init checks done")

logging.info("Start metrics server")
start_http_server(8000)


def GetSystemId(system_name):
    try:
        r = requests.get(
            f'https://esi.evetech.net/latest/search/?categories=solar_system&datasource=tranquility&language=en&search={system_name}&strict=false')
        ESI_CALL.inc()
    except requests.ConnectionError:
        logging.error(f'Failed to connect to ESI')
        sys.exit()

    json_data = r.json()

    if len(json_data) > 0:
        logging.debug(f'Got {system_name} ID - {json_data["solar_system"]}')
        return json_data["solar_system"]
    else:
        logging.warning(f'Could not find ID for {system_name}')
        return None


def GetRouteLenght(source, destination):
    try:
        r = requests.get(f'https://esi.evetech.net/latest/route/{source}/{destination}/')
        ESI_CALL.inc()
    except requests.ConnectionError:
        logging.error(f'Failed to connect to ESI')
        return 0

    if r.status_code == 200:
        json_data = r.json()
        return len(json_data) - 1
    else:
        return 0


logging.info("Geting systems IDs")

for system_name in system_names:
    system_id = GetSystemId(system_name)
    if system_id is not None:
        system_ids.extend(system_id)

if len(system_ids) == 0:
    logging.error("No systems to check routes")
    sys.exit()

logging.info(f"Total: {system_ids}")

# FIXME
# Get current ID
r = requests.get("https://www.eve-scout.com/api/wormholes")
json_data = r.json()
sorted_data = sorted(json_data, key=lambda k: k.get('id'), reverse=True)

logging.info(f'Current ID: {sorted_data[0]["id"]}')

lastscoutid = sorted_data[0]["id"]
newlastscoutid = sorted_data[0]["id"]

time.sleep(60)

# Main loop
while (True):

    logging.info("Get new data from eve-scout")
    logging.debug(f'Last Scout ID - {lastscoutid}')
    try:
        r = requests.get("https://www.eve-scout.com/api/wormholes")
    except requests.ConnectionError:
        logging.error(f'Failed to connect to eve-scout')
        time.sleep(60)
        continue
    if r.status_code != 200:
        logging.warning(f"Got unexpected response: {r.status_code}")
        time.sleep(60)
        continue

    json_data = r.json()
    if len(json_data) == 0:
        logging.warning(f"Got empty data?")
        SCOUT_DATA = len(json_data)
        time.sleep(60)
        continue
    else:
        logging.info(f"Got {len(json_data)} items")
        SCOUT_DATA.set(len(json_data))

    for element in json_data:

        scoutid = int(element["id"])

        if scoutid > newlastscoutid:
            logging.debug(f"[NSID: {newlastscoutid}] - Got bigger scoutid, save for future")
            newlastscoutid = scoutid

        if scoutid > lastscoutid:

            logging.info(f"[SID: {lastscoutid}] - Got bigger scoutid {scoutid}, process element")

            dst_region_name = element["destinationSolarSystem"]["region"]["name"]
            dst_system_name = element["destinationSolarSystem"]["name"]
            dst_system_id = element["destinationSolarSystem"]["id"]
            wh_sig_in = element["wormholeDestinationSignatureId"]
            wh_sig_out = element["signatureId"]

            for idx, system in enumerate(system_ids):
                distance = GetRouteLenght(element["wormholeDestinationSolarSystemId"], system)
                if distance == 0:
                    logging.info(f'Thera in {dst_region_name}-{dst_system_name} - no route from {system_names[idx]}')
                elif distance <= THERA_MAXDISTANCE:
                    logging.info(
                        f'Thera in {dst_region_name}-{dst_system_name} - {distance} jumps from {system_names[idx]}')
                    msg = f"""
**Новая WH в Thera**\n
```
{dst_system_name} < {dst_region_name} - {distance} от {system_names[idx]}\n
Вход - {wh_sig_in} / Выход - {wh_sig_out}\n
```
[Маршрут на Dotlan](<https://evemaps.dotlan.net/route/{system_names[idx]}:{dst_system_name}>)
"""
                    logging.debug("Notify users")
                    requests.post(THERA_WEBHOOK, data={'content': msg})
                    DISCORD_CALL.inc()
                else:
                    logging.info(
                        f'Thera in {dst_region_name}-{dst_system_name} - {distance} jumps from {system_names[idx]}')
        else:

            logging.debug(f'Skip Scout ID {scoutid} < {lastscoutid}')

    lastscoutid = newlastscoutid
    logging.debug(f'Set new ID: {lastscoutid}')
    time.sleep(60)
