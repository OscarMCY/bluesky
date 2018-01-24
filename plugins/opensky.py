"""A plugin for playing air traffic from the OpenSky Network.

The plugin gets current traffic from the OpenSky Network and makes aircraft
move.

The OpenSky Python API allows for a number of unauthenticated requests. If you
feed the network, you are allowed an unlimited number of requests for the data
you feed. If you set `opensky_user` and `opensky_password` in your settings.cfg
file and the latest request you send fails, the program falls back to the data
from your sensors.

Xavier Olive, 2018
Joost Ellerbroek, 2018
"""
import time
import requests
import numpy as np

from bluesky import stack, settings, traf, scr
from bluesky.tools import RegisterElementParameters, TrafficArrays
settings.set_variable_defaults(opensky_user=None, opensky_password=None)

# Globals
reader = None

def init_plugin():
    global reader
    reader = OpenSkyListener()

    config = {
        'plugin_name': 'OPENSKY',
        'plugin_type': 'sim',
        'update_interval': 3.0,
        'preupdate': reader.update
    }

    stackfunctions = {
        'OPENSKY': [
            'OPENSKY [on/off]',
            '[onoff]',
            reader.toggle,
            'Select OpenSky as a data source for traffic']
    }

    return config, stackfunctions


class OpenSkyListener(TrafficArrays):
    def __init__(self):
        super(OpenSkyListener, self).__init__()
        if settings.opensky_user:
            self._auth = (settings.opensky_user, settings.opensky_password)
        else:
            self._auth = ()
        self._api_url = "https://opensky-network.org/api"
        self.connected = False

        with RegisterElementParameters(self):
            self.upd_time = np.array([])
            self.my_ac = np.array([], dtype=np.bool)

    def create(self, n=1):
        super(OpenSkyListener, self).create(n)
        # Store creation time of new aircraft
        self.upd_time[-n:] = time.time()
        self.my_ac[-n:] = False

    def get_json(self, url_post, params=None):
        r = requests.get(self._api_url + url_post, auth=self._auth, params=params)
        if r.status_code == 200:
            return r.json()

        # "Response not OK. Status {0:d} - {1:s}".format(r.status_code, r.reason)
        return None

    def get_states(self, ownonly=False):
        url_post = '/states/{}'.format('own' if ownonly else 'all')
        states_json = self.get_json(url_post)
        if states_json is not None:
            return list(zip(*states_json['states']))
        return None

    def update(self):
        if not self.connected:
            return

        # Get states from OpenSky. If all states fails try getting own states only.
        states = self.get_states()
        if states is None:
            if self.authenticated:
                states = self.get_states(ownonly=True)
            if states is None:
                return

        # Current time
        curtime = time.time()

        # States contents:
        icao24, acid, orig, time_pos, last_contact, lon, lat, geo_alt, on_gnd, \
            spd, hdg, vspd, sensors, baro_alt, squawk, spi, pos_src = states[:17]

        # Relevant params as numpy arrays
        lat = np.array(lat, dtype=np.float64)
        lon = np.array(lon, dtype=np.float64)
        alt = np.array(baro_alt, dtype=np.float64)
        hdg = np.array(hdg, dtype=np.float64)
        vspd = np.array(vspd, dtype=np.float64)
        spd = np.array(spd, dtype=np.float64)
        idx = np.array([traf.id2idx(acidi) for acidi in acid])

        # Split between already existing and new aircraft
        newac = idx == -1
        other = np.logical_not(newac)

        # Filter out invalid entries
        valid = np.logical_not(np.logical_or.reduce(
            [np.isnan(x) for x in [lat, lon, alt, hdg, vspd, spd]]))
        newac = np.logical_and(newac, valid)
        other = np.logical_and(other, valid)
        n_new = np.count_nonzero(newac)
        n_oth = np.count_nonzero(other)

        # Create new aircraft
        if n_new:
            newacid = [newid for newid, isnew in zip(acid, newac) if isnew]
            traf.create(n_new, 'B744', alt[newac], spd[newac], None,
                        lat[newac], lon[newac], hdg[newac], newacid)
            self.my_ac[-n_new:] = True

        # Update the rest
        if n_oth:
            traf.move(idx[other], lat[other], lon[other], alt[other], hdg[other], \
                      spd[other], vspd[other])
            self.upd_time[idx[other]] = curtime

        # remove aircraft with no message for less than 1 minute
        # opensky already filters
        delidx = np.logical_and(self.my_ac, curtime - self.upd_time > 60)
        if np.any(delidx):
            traf.delete(delidx)
            scr.echo('Deleting {} aircraft'.format(np.count_nonzero(delidx)))

    def toggle(self, flag=None):
        if flag:
            self.connected = True
            stack.stack('OP')
            return True, 'Connecting to OpenSky'
        else:
            self.connected = False
            return True, 'Stopping the requests'
