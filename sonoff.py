# The domain of your component. Should be equal to the name of your component.
import logging, time, hmac, hashlib, random, base64, json, socket, requests
import voluptuous as vol

# from homeassistant.components.switch import SwitchDevice
from datetime import timedelta
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.async_ import run_coroutine_threadsafe
from homeassistant.helpers import discovery
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (EVENT_HOMEASSISTANT_STOP, CONF_SCAN_INTERVAL, CONF_EMAIL, CONF_PASSWORD)
# from homeassistant.util import Throttle

CONF_WSHOST     = 'wshost'
CONF_APIHOST    = 'apihost'

SCAN_INTERVAL = timedelta(seconds=60)
DOMAIN = "sonoff"

REQUIREMENTS = ['uuid', 'websocket-client']

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_APIHOST, default='eu-api.coolkit.cc'): cv.string,
        vol.Optional(CONF_WSHOST, default='eu-long.coolkit.cc'): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=SCAN_INTERVAL): cv.time_period,
    }),
}, extra=vol.ALLOW_EXTRA)

def gen_nonce(length=8):
    """Generate pseudorandom number."""
    return ''.join([str(random.randint(0, 9)) for i in range(length)])

async def async_setup(hass, config):
    """Set up the eWelink/Sonoff component."""
    
    # get email & password from configuration.yaml
    email       = config.get(DOMAIN, {}).get(CONF_EMAIL,'')
    password    = config.get(DOMAIN, {}).get(CONF_PASSWORD,'')
    apihost     = config.get(DOMAIN, {}).get(CONF_APIHOST,'')
    wshost      = config.get(DOMAIN, {}).get(CONF_WSHOST,'')

    _LOGGER.debug("Create Sonoff object")

    hass.data[DOMAIN] = Sonoff(hass, email, password, apihost, wshost)

    for component in ['switch']:
        discovery.load_platform(hass, component, DOMAIN, {}, config)

    # maybe close websocket with this (if it runs)
    # hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, hass.data[DOMAIN].restore_all())

    def update_devices(event_time):
        """Refresh"""     
        _LOGGER.debug("Updating main Sonoff...")

        # @REMINDER figure it out how this works exactly
        run_coroutine_threadsafe( hass.data[DOMAIN].async_update(), hass.loop)

    async_track_time_interval(hass, update_devices, SCAN_INTERVAL)

    return True

class Sonoff():
    def __init__(self, hass, email, password, apihost, wshost):
        self._hass      = hass
        self._email     = email
        self._password  = password
        self._apihost   = apihost
        self._wshost    = wshost

        self._devices   = []
        self._ws        = None

        self.do_login()

    def do_login(self):
        import uuid

        # @TODO add a fallback for other login regions too
        
        app_details = {
            'email'     : self._email,
            'password'  : self._password,
            'version'   : '6',
            'ts'        : int(time.time()),
            'nonce'     : gen_nonce(15),
            'appid'     : 'oeVkj2lYFGnJu5XUtWisfW4utiN4u9Mq',
            'imei'      : str(uuid.uuid4()),
            'os'        : 'iOS',
            'model'     : 'iPhone10,6',
            'romVersion': '11.1.2',
            'appVersion': '3.5.3'
        }

        decryptedAppSecret = b'6Nz4n0xA8s8qdxQf2GqurZj2Fs55FUvM'

        hex_dig = hmac.new(
            decryptedAppSecret, 
            str.encode(json.dumps(app_details)), 
            digestmod=hashlib.sha256).digest()
        
        sign = base64.b64encode(hex_dig).decode()

        self._headers = {
            'Authorization' : 'Sign ' + sign,
            'Content-Type'  : 'application/json'
        }

        r = requests.post('https://{}:8080/api/user/login'.format(self._apihost), 
            headers=self._headers, json=app_details)
        
        self._bearer_token  = r.json()['at']
        self._apikey        = r.json()['user']['apikey']
        self._headers.update({'Authorization' : 'Bearer ' + self._bearer_token})

        self.update_devices() # to write the devices list 

    def update_devices(self):
        r = requests.get('https://{}:8080/api/user/device'.format(self._apihost), 
            headers=self._headers)

        self._devices = r.json()

        if 'error' in self._devices and self._devices['error'] in [400, 401]:
            _LOGGER.warning("re-login")
            # @TODO maybe add a grace period before re-login to allow the user to make real life app changes 
            # (mabe even add a service call / switch to deactivate sonoff component) 
            self.do_login()

        return self._devices

    def get_devices(self, force_update = False):
        if force_update: 
            return self.update_devices()

        return self._devices

    def get_bearer_token(self):
        return self._bearer_token

    def get_apikey(self):
        return self._apikey

    # async def async_get_devices(self):
    #     return self.get_devices()

    async def async_update(self):
        # devs = await self.async_get_devices()
        devices = self.update_devices()

    def _get_ws(self):
        """Check if the websocket is setup and connected."""
        try:
            create_connection
        except:
            from websocket import create_connection

        if self._ws is None:
            try:
                self._ws = create_connection(('wss://{}:8080/api/ws'.format(self._wshost)), timeout=2)

                payload = {
                    'action'    : "userOnline",
                    'userAgent' : 'app',
                    'version'   : 6,
                    'nonce'     : gen_nonce(15),
                    'apkVesrion': "1.8",
                    'os'        : 'ios',
                    'at'        : self.get_bearer_token(),
                    'apikey'    : self.get_apikey(),
                    'ts'        : str(int(time.time())),
                    'model'     : 'iPhone10,6',
                    'romVersion': '11.1.2',
                    'sequence'  : str(time.time()).replace('.','')
                }

                self._ws.send(json.dumps(payload))
                wsresp = self._ws.recv()
                # _LOGGER.error("open socket: %s", wsresp)

            except (socket.timeout, ConnectionRefusedError, ConnectionResetError):
                self._ws = None

        return self._ws
        
    def switch(self, deviceid, newstate):
        """Switch on or off."""
        self._ws = self._get_ws()

        _LOGGER.debug("Switching `%s` to state: %s", deviceid, newstate)

        # convert from True/False to on/off
        if isinstance(newstate, (bool)):
            newstate = 'on' if newstate else 'off'

        payload = {
            'action'        : 'update',
            'userAgent'     : 'app',
            'params'        : {
                'switch' : newstate
            },
            'apikey'        : str(self.get_apikey()),
            'deviceid'      : str(deviceid),
            'sequence'      : str(time.time()).replace('.',''),
            'controlType'   : 4,
            'ts'            : 0
        }

        self._ws.send(json.dumps(payload))
        wsresp = self._ws.recv()
        # _LOGGER.error("switch socket: %s", wsresp)
        
        self._ws.close() # no need to keep websocket open (for now)
        self._ws = None

        # set also te pseudo-internal state of the device
        for idx, device in enumerate(self._devices):
            if device['deviceid'] == deviceid:
                self._devices[idx]['params']['switch'] = newstate

        # @TODO add some sort of validation here, maybe call the devices status 
        # only IF MAIN STATUS is done over websocket exclusively

        return newstate

class SonoffDevice(Entity):
    """Representation of a Sonoff device"""

    def __init__(self, hass, device):
        """Initialize the device."""

        self._hass          = hass
        self._name          = 'sonoff_{}'.format(device['deviceid']) 
        self._device_name   = device['name']
        self._deviceid      = device['deviceid']
        self._apikey        = device['apikey']
        self._state         = device['params']['switch'] == 'on'
        self._available     = device['online']

        self._attributes    = {
            'device_name'   : self._device_name,
            'device_id'     : self._deviceid
        }

    def get_device(self, deviceid):
        for device in self._hass.data[DOMAIN].get_devices():
            if 'deviceid' in device and device['deviceid'] == deviceid:
                return device

        return None

    def get_state(self, deviceid):
        device = self.get_device(deviceid)
        return device['params']['switch'] == 'on' if device else False

    def get_available(self, deviceid):
        device = self.get_device(deviceid)
        return device['online'] if device else False

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def is_on(self):
        """Return true if device is on."""
        return self.get_state(self._deviceid)

    @property
    def available(self):
        """Return true if device is online."""
        return self.get_available(self._deviceid)

    # @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Update device state."""

        # we don't update here because there's 1 single thread that can be active at anytime
        # i.e. eWeLink API allows only 1 active session
        pass

    def turn_on(self, **kwargs):
        """Turn the device on."""
        self._state = self._hass.data[DOMAIN].switch(self._deviceid, True)
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self._state = self._hass.data[DOMAIN].switch(self._deviceid, False)
        self.schedule_update_ha_state()

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        return self._attributes
