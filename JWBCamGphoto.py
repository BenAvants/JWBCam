#!/usr/bin/python3

# Nikon idVendor = 0x04b0 , (dec) 1200
# Canon idVendor = 0x04a9 , (dec) 1193

import logging
import gphoto2 as gp
import json
import mmap
from threading import Thread, Event
from collections import OrderedDict
from usb.core import find as finddev
from io import BytesIO as BIO
from time import sleep
from weakref import WeakSet
from sys import byteorder as BO
import pyudev

# GPhoto2Error - gphoto2 error class
global MV

def typeName(type):
	if type == gp.GP_WIDGET_SECTION:
		return 'section'
	if type == gp.GP_WIDGET_TEXT:
		return 'text'
	if type == gp.GP_WIDGET_RANGE:
		return 'range'
	if type == gp.GP_WIDGET_TOGGLE:
		return 'checkbox' # 'toggle'
	if type == gp.GP_WIDGET_RADIO:
		return 'radio'
	if type == gp.GP_WIDGET_MENU:
		return 'menu'
	if type == gp.GP_WIDGET_DATE:
		return 'date'

def dictSettings(config):
	settings = OrderedDict()
	def recurseChildren(entity):
		if entity.count_children() > 0:
			if 'Camera and Driver Configuration' not in entity.get_label():
				settings[entity.get_name()] = (entity.get_label(),'tab',[])
			for child in entity.get_children():
				recurseChildren(child)
		else:
			type = entity.get_type()
			label = entity.get_label()
			val = entity.get_value()
			choices = []
			if type == 5:
				for ii in range(entity.count_choices()):
					choices.append(entity.get_choice(ii))
			settings[entity.get_name()] = (label, typeName(type), val, choices)
	recurseChildren(config)
	return settings

class ModuleVariables():
	def __init__(self):
		self.camera = None
		self.context = None
		self.bus = None
		self.address = None
		self.pluggedin = False
		self.config = None
		self.serialNumber = None
		self.settings = None
		self.jsettings = None
		self.ready = False
		self.USBevent = None
		self.cameraError = None
		
		self.previewing = False
		self.previewer = None
		self.previewInterval = 0.1
		self.capturing = False
		self.lastImage = None
		self.fname = None
		self.mmjpg = None
		
		self.imageClients = WeakSet()
		self.settingsClients = WeakSet()
		
		self.running = False
		self.serveSock = None
		self.message = None
		
MV = ModuleVariables()

def initialize():
	global MV
	while MV.running:
		while MV.running and MV.ready:
			if MV.cameraError.wait(0.1):
				logging.warning('Camera Lost')
				reset()
				MV.cameraError.clear()
		MV.USBevent.set()
		while MV.running and not MV.ready:
			if MV.USBevent.wait(0.1):
				MV.USBevent.clear()
				try:
					MV.camera = gp.Camera()
					MV.camera.init(MV.context)
				except gp.GPhoto2Error as e:
					MV.camera = None
					MV.config = None
					MV.settings = None
					MV.ready = False
					if e.code == gp.GP_ERROR_MODEL_NOT_FOUND:
						continue
					logging.error('Initialization failed')
					logging.error(e)
				except Exception as e:
					logging.error(e)
					raise e
				else:
					MV.pluggedin = True
					while MV.running and not MV.ready:
						try:
							MV.serialNumber = MV.camera.get_single_config('serialnumber').get_value()
							bus, address = MV.camera.get_port_info().get_path()[4:].split(',')
							MV.bus = int(bus)
							MV.address = int(address)
							MV.config = MV.camera.get_config()
							MV.settings = dictSettings(MV.config)
							MV.jsettings = json.dumps(MV.settings).encode('utf-8')
							MV.ready = True
							logging.info('Camera Initialized')
						except gp.GPhoto2Error as e:
							logging.error(e)
							sleep(0.1)

def purge_events(timeout = 100):
	global MV
	if not MV.ready:
		return
	code = gp.GP_EVENT_UNKNOWN
	count = 0
	while code != gp.GP_EVENT_TIMEOUT:
		try:
			code, ed = MV.camera.wait_for_event(timeout)
			logging.debug(ed)
		except gp.GPhoto2Error as e:
			sleep(0.1)
			count += 1
			if count > 10:
				MV.cameraError.set()
				return
		except Exception as e:
			MV.cameraError.set()
			logging.error(e)
			raise e
	while code != gp.GP_EVENT_TIMEOUT:
		if code == gp.GP_EVENT_UNKNOWN:
			logging.debug(ed)
		elif code in (gp.GP_EVENT_FILE_ADDED, gp.GP_EVENT_FOLDER_ADDED):
			logging.info(ed)
		else:
			logging.warning(ed)
		code, ed = MV.camera.wait_for_event(timeout)

def release():
	global MV
	if MV.camera is not None and MV.pluggedin:
		MV.camera.exit()
		sleep(0.05)
		logging.info('Camera Released')
		MV.camera = None
	MV.config = None
	MV.settings = None
	MV.ready = False

def reset():
	global MV
	release()
	try:
		dev = finddev(idVendor=0x04b0)
		if dev:
			dev.reset()
			logging.info('Nikon reset')
		else:
			dev = finddev(idVendor=0x04a9)
			if dev:
				dev.reset()
				logging.info('Canon reset')
	except Exception as e:
		logging.error('Reset Failed')
		logging.error(e)

def changeSetting(name,value):
	global MV
	if not MV.ready:
		return
	logging.debug(name)
	while capturing:
		sleep(0.05)
	busy = True
	while busy and MV.ready:
		try:
			MV.config.get_child_by_name(name).set_value(value)
			gp.gp_camera_set_config(MV.camera,MV.config)
			MV.settings = dictSettings(MV.config)
			MV.jsettings = json.dumps(MV.settings).encode('utf-8')
			logging.info('setting changed and updated')
			for client in MV.settingsClients:
				client.set()
			busy = False
		except gp.GPhoto2Error as e:
			if e.code == gp.GP_ERROR_CAMERA_BUSY:
				pass
			else:
				logging.error('Setting Change Failed')
				logging.error(e)
				busy = False
				MV.cameraError.set()
		except Exception as e:
			logging.error('Setting Change Critically Failed')
			logging.error(e)

def preview(newClient = None):
	global MV
	if not MV.ready:
		logging.info('Preview not started - Camera not ready')
		return
	if newClient is not None:
		registerImageClient(newClient)
	if not MV.previewing and not MV.capturing:
		MV.previewer = Thread(target=updatePreview, daemon=True)
		MV.previewing = True
		MV.previewer.start()
		logging.info('Preview Started')
	elif MV.previewing:
		logging.info('Already previewing')
	elif MV.capturing:
		logging.info('Capturing - cannot start preview')

def stopPreview():
	global MV
	if MV.previewing:
		MV.previewing = False
		while MV.previewer.is_alive() and MV.running:
			MV.previewer.join(0.001)
		MV.previewer = None
		logging.info('Preview Stopped')
	else:
		logging.debug('Not Previewing - nothing to stop')
		
def updatePreview():
	global MV
	MV.capturing = True
	busy = True
	while busy and MV.ready:
		try:
			logging.debug('Trying to capture first preview frame')
			cap = MV.camera.capture_preview()
			busy = False
		except gp.GPhoto2Error as e:
			if e.code == gp.GP_ERROR_CAMERA_BUSY:
				pass
			else:
				logging.error('Capture-preview failed')
				logging.error(e)
				busy = False
				MV.previewing = False
				purge_events()
	MV.capturing = False
	try:
		while MV.previewing and MV.ready:
			lastImage = BIO(cap.get_data_and_size()).getvalue()
			MV.mmjpg.seek(0)
			howMany = MV.mmjpg.write(lastImage)
			for client in MV.imageClients:
				client.sendall(MV.fname.encode('utf-8') + '&' + str(howMany).encode('utf-8'))
			sleep(MV.previewInterval)
			while len(MV.imageClients) == 0 and MV.previewing and MV.ready:
				sleep(MV.previewInterval)
			busy = True
			MV.capturing = True
			while busy and MV.ready:
				try:
					logging.debug('Trying to capture preview frame')
					cap = MV.camera.capture_preview()
					busy = False
				except gp.GPhoto2Error as e:
					if e.code == gp.GP_ERROR_CAMERA_BUSY:
						pass
					else:
						logging.error('Capture-preview failed')
						logging.error(e)
						MV.previewing = False
						purge_events()
			MV.capturing = False
	finally:
		MV.previewing = False
		purge_events()

def registerImageClient(newClient):
	global MV
	if not newClient in MV.imageClients:
		MV.imageClients.add(newClient)
		logging.info('Image Client Added')

def registerStatusClient(newClient):
	global MV
	if not newClient in MV.imageClients:
		MV.settingsClients.add(newClient)
		logging.info('Status Client Added')

def captureImage():
	global MV
	if not MV.ready or MV.previewing or MV.capturing:
		logging.info('Cannot Capture Image')
		return
	MV.capturing = True
	busy = True
	while busy and MV.ready:
		try:
			logging.debug('Trying to capture image')
			capinfo = MV.camera.capture(gp.GP_CAPTURE_IMAGE)
			cap = MV.camera.file_get(capinfo.folder, capinfo.name, gp.GP_FILE_TYPE_NORMAL)
			busy = False
		except gp.GPhoto2Error as e:
			if e.code == gp.GP_ERROR_CAMERA_BUSY:
				pass
			else:
				logging.error('Capture image failed')
				logging.error(e)
				MV.previewing = False
				purge_events()
	busy = True
	while busy and MV.ready:
		try:
			cap = MV.camera.file_get(capinfo.folder, capinfo.name, gp.GP_FILE_TYPE_NORMAL)
			busy = False
			purge_events()
		except gp.GPhoto2Error as e:
			if e.code == gp.GP_ERROR_CAMERA_BUSY:
				pass
			else:
				logging.error('Transfer image failed')
				logging.error(e)
				MV.previewing = False
				purge_events()
	MV.capturing = False
	lastImage = BIO(cap.get_data_and_size()).getvalue()
	MV.mmjpg.seek(0)
	howMany = MV.mmjpg.write(lastImage)
	for client in MV.imageClients:
		client.sendall(MV.fname.encode('utf-8') + '&' + str(howMany).encode('utf-8'))

def isReady():
	global MV
	return str(MV.ready).encode('utf-8')

def isPreviewing():
	global MV
	return str(MV.previewing).encode('utf-8')

def isCapturing():
	global MV
	return str(MV.capturing).encode('utf-8')

def getSettings():
	global MV
	# client.sendall(len(MV.jsettings).to_bytes(2, BO))
	# client.sendall(MV.jsettings)
	return len(MV.jsettings).to_bytes(2, BO) + MV.jsettings

if __name__ == '__main__':
	import signal
	import socket
	from select import select
	from sys import exit as EXIT
	import sys
	import os
	# from inspect import signature as sig
	
	MV.USBevent = Event()
	def USBListener(action, device):
		global MV
		if 'remove' in action and MV.ready:
			dev = finddev(bus=MV.bus, address=MV.address)
			if not dev:
				MV.pluggedin = False
				MV.cameraError.set()
		elif 'add' in action:
			MV.USBevent.set()
	usbmonitor = pyudev.Monitor.from_netlink(pyudev.Context())
	usbmonitor.filter_by('usb')
	usbmonitor.filter_by_tag('uaccess')
	usbobserver = pyudev.MonitorObserver(usbmonitor, USBListener)
	usbobserver.start()
	
	initializer = None
	
	# Define client accessible functions and their signatures to modularize the command parsing
	clientFuncs = {'initialize':{'func':initialize,'client':False,'params':()}, 
				   'release':{'func':release,'client':False,'params':()}, 
				   'reset':{'func':reset,'client':False,'params':()}, 
				   'changeSetting':{'func':changeSetting,'client':False,'params':('name','value')}, 
				   'preview':{'func':preview,'client':True,'params':()}, 
				   'stopPreview':{'func':stopPreview,'client':False,'params':()}, 
				   'captureImage':{'func':captureImage,'client':False,'params':()}, 
				   'isReady':{'func':isReady,'client':False,'params':()}, 
				   'isPreviewing':{'func':isPreviewing,'client':False,'params':()}, 
				   'isCapturing':{'func':isCapturing,'client':False,'params':()}, 
				   'getSettings':{'func':getSettings,'client':False,'params':()}
				   }
	
	loglevel = logging.INFO
	for arg in sys.argv:
		if 'log-level' in arg:
			if 'error' in arg:
				loglevel = logging.ERROR
			elif 'warning' in arg:
				loglevel = logging.WARNING
			elif 'info' in arg:
				loglevel = logging.INFO
			elif 'debug' in arg:
				loglevel = logging.DEBUG
	
	logging.basicConfig(
		format='%(levelname)s: %(name)s: JWBCam: %(message)s', level=loglevel)
	gp.check_result(gp.use_python_logging())
	
	class StopGuard:
		kill_now = False
		def __init__(self):
			signal.signal(signal.SIGINT, self.exit_gracefully)
			signal.signal(signal.SIGTERM, self.exit_gracefully)

		def exit_gracefully(self,signum, frame):
			self.kill_now = True
			MV.cameraError.set()
			raise SystemExit('StopGuard intercepted TERM signal')

	stoppingGuard = StopGuard()
	
	def clientListener():
		global MV
		try:
			messagesock, addr = MV.serveSock.accept()
			messagesock.settimeout(0.01)
		except:
			return
		connected = True
		while connected and MV.running:
			try:
				chunk = messagesock.recv(128)
			except socket.timeout:
				continue
			except:
				raise
			else:
				if chunk:
					message = chunk.decode('utf-8')
					chunk = None
					reply = parseMessage(message, messagesock)
					if reply is not None:
						try:
							messagesock.sendall(reply)
						except:
							pass
						reply = None
					message = None
				else:
					messagesock.close()
					connected = False
		if connected:
			messagesock.close()
	
	def parseMessage(message, client):
		global MV
		parts = message.split(',')
		cmd = clientFuncs[parts[0]]
		func = cmd['func']
		params = cmd['params']
		pvals = parts[1:]
		kwargs = {}
		if len(params) == len(pvals)
			for kw, ind in enumerate(params):
				kwargs[kw] = pvals[ind]
		if cmd['client']:
			kwargs['client'] = client
		return func(**kwargs)
	
	if not os.path.exists('./JWBCamImage.jpg'):
		with open('./JWBCamImage.jpg','w+') as f:
			f.write('\00' * 1024*1024*20) # Write 20 MB of null values to the file to init
			f.flush()
	fimg = open('./JWBCamImage.jpg','r+b')
	MV.fname = os.path.realpath(fimg.name)
	MV.mmjpg = mmap.mmap(fimg.fileno(),0)
	
	# Unlink / bind unix socket and listen for incoming connections
	# if os.path.exists('/var/tmp/JWBCamGpPi.pe'):
		# try:
			# os.unlink('/var/tmp/JWBCamGpPi.pe')
		# except OSError:
			# if os.path.exists('/var/tmp/JWBCamGpPi.pe'):
				# raise
	MV.serveSock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	MV.serveSock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
	MV.serveSock.bind('\0JWBCamGpPi.pe')
	MV.serveSock.listen(10)
	
	communicationClients = []
	
	MV.cameraError = Event()
	MV.context = gp.Context()
	
	MV.running = True
	
	initializer = Thread(target=initialize)
	initializer.start()
	
	try:
		runcount = 0
		while MV.running:
			r,w,e = select((MV.serveSock,),[],[],0.05)
			for request in r:
				t = Thread(target=clientListener)
				communicationClients.append(t)
				t.start()
				logging.info('Client connected')
			
			for client in communicationClients:
				if not client.is_alive():
					client.join(1)
					communicationClients.remove(client)
			
	finally:
		MV.running = False
		MV.ready = False
		sleep(0.001)
		MV.previewing = False
		if MV.previewer is not None:
			while MV.previewer.is_alive():
				MV.previewer.join(.001)
			MV.previewer = None
		for client in communicationClients:
			try:
				client.shutdown(socket.SHUT_RDWR)
				client.close()
			except OSError as e:
				logging.error('Communication client shutdown/close error')
				logging.error(e)
			del(client)
		try:
			MV.serveSock.shutdown(socket.SHUT_RDWR)
			MV.serveSock.close()
		except OSError as e:
			logging.error('Server Socket shutdown/close error')
			logging.error(e)
		running = False
		if initializer is not None:
			initializer.join(.001)
			initializer = None
		# try:
			# reset()
		# except Exception as e:
			# logging.error('Camera Exit Error')
			# logging.error(e)
		