#!/usr/bin/python3

import logging
import gphoto2 as gp
import json
from threading import Thread, Event
from multiprocessing import Event as mpEvent
from collections import OrderedDict
from usb.core import find as finddev
from io import BytesIO as BIO
from time import sleep
from weakref import WeakSet

# GPhoto2Error - gphoto2 error class

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

global camera, context, config, settings, ready, previewing, previewer, previewInterval
global capturing, lastImage, settingsClients, imageClients, streamUrl, bus, address, serialNumber
global update_event

logging.basicConfig(
	format='%(levelname)s: %(name)s: JWBCam: %(message)s', level=logging.INFO)
gp.check_result(gp.use_python_logging())

camera = gp.Camera()
context = gp.Context()

config = None
settings = None
ready = False
previewing = False
previewer = None
previewInterval = 0.1
capturing = False
lastImage = None
imageClients = WeakSet()
settingsClients = WeakSet()
streamUrl = None
bus = None
address = None
serialNumber = None
update_event = None

class jwbcamera():
	def __init__(self,updateEvent=None,logLevel=logging.INFO):
		global update_event
		# self.previewing = False
		# self.previewer = None
		# self.previewInterval = 0.1
		# self.capturing = False
		# self.lastImage = None
		# self.needsReset = True
		# self.streamUrl = streamUrl
		# self.clients = WeakSet()
		# try:
			# self.context = gp.Context()
			# self.camera = gp.Camera()
			# self.camera.init(self.context)
		# except gp.GPhoto2Error as e:
			# logging.error('Init failed')
			# logging.error(e)
			# self.camera = None
			# self.bus = None
			# self.address = None
			# self.config = None
			# self.settings = None
			# self.ready = False
			# self.updateEvent.set()
		# except Exception as e:
			# raise e
		# else:
			# self.serialNumber = self.camera.get_single_config('serialnumber').get_value()
			# self.bus, self.address = self.camera.get_port_info().get_path()[4:].split(',')
			# self.bus = int(self.bus)
			# self.address = int(self.address)
			# self.config = self.camera.get_config()
			# self.settings = dictSettings(self.config)
			# self.ready = True
			# self.updateEvent.set()
		if update_event is None:
			update_event = updateEvent
		logging.basicConfig(level=logLevel)
		pass
	
	def reset(self):
		global bus, address, context, camera, config, settings, ready, serialNumber
		if camera is not None:
			camera.exit()
		if bus is not None:
			dev = finddev(bus=bus, address=address)
			dev.reset()
			sleep(.5)
		try:
			context = gp.Context()
			camera = gp.Camera()
			camera.init(context)
		except gp.GPhoto2Error as e:
			logging.error('Reset failed')
			logging.error(e)
			camera = None
			bus = None
			address = None
			config = None
			settings = None
			ready = False
		except Exception as e:
			logging.error(e)
			raise e
		else:
			ready = False
			while not ready:
				try:
					serialNumber = camera.get_single_config('serialnumber').get_value()
					bus, address = camera.get_port_info().get_path()[4:].split(',')
					bus = int(bus)
					address = int(address)
					config = camera.get_config()
					settings = dictSettings(config)
					ready = True
				except gp.GPhoto2Error as e:
					logging.error(e)
					sleep(0.1)
					
	def initialize(self):
		global ready, context, camera, bus, address, config, settings, serialNumber, update_event
		if ready:
			logging.info('Already Initialized')
			return
		try:
			context = gp.Context()
			camera = gp.Camera()
			camera.init(context)
		except gp.GPhoto2Error as e:
			logging.error('Initialize failed')
			logging.error(e)
			camera = None
			bus = None
			address = None
			config = None
			settings = None
			ready = False
		except Exception as e:
			logging.error(e)
			raise e
		else:
			ready = False
			while not ready:
				try:
					serialNumber = camera.get_single_config('serialnumber').get_value()
					bus, address = camera.get_port_info().get_path()[4:].split(',')
					bus = int(bus)
					address = int(address)
					config = camera.get_config()
					settings = dictSettings(config)
					ready = True
					logging.info('Camera Initialized')
					if update_event is not None:
						update_event.set()
				except gp.GPhoto2Error as e:
					logging.error(e)
					sleep(0.1)
	
	def changeSetting(self,name,value):
		global config, camera, ready, settings, capturing
		if not ready:
			return
		logging.debug(name)
		while capturing:
			sleep(0.05)
		config.get_child_by_name(name).set_value(value)
		gp.gp_camera_set_config(camera,config)
		settings = dictSettings(config)
		logging.info('setting changed and updated')
	
	def preview(self,newClient=None):
		global previewing, previewer, capturing, ready
		if not ready:
			return
		if newClient is not None:
			self.registerClient(newClient)
		if not previewing and not capturing:
			previewer = Thread(target=self.updatePreview, daemon=True)
			previewer.start()
			logging.info('Preview Started')
	
	def updatePreview(self):
		global ready, camera, lastImage, imageClients, previewInterval, previewing, capturing
		if not ready:
			return
		try:
			capturing = True
			cap = camera.capture_preview()
			capturing = False
		except gp.GPhoto2Error as e:
			self.reset
			sleep(0.1)
			if ready:
				cap = camera.capture_preview()
			else:
				raise e
		try:
			previewing = True
			while previewing:
				lastImage = BIO(cap.get_data_and_size()).getvalue()
				for client in imageClients:
					client.set()
				sleep(previewInterval)
				while len(imageClients) == 0 and previewing:
					sleep(previewInterval)
				try:
					logging.debug('Trying to capture preview frame')
					cap = camera.capture_preview()
				except gp.GPhoto2Error as e:
					self.reset
					if ready:
						cap = camera.capture_preview()
					else:
						raise e
		finally:
			previewing = False
			self.purge_events()
	
	def registerClient(self,clientEvent):
		global imageClients
		imageClients.add(clientEvent)
	
	def capture_image(self):
		global ready, camera, lastImage, imageClients
		if not ready:
			return
		try:
			capinfo = camera.capture(gp.GP_CAPTURE_IMAGE)
		except gp.GPhoto2Error as e:
			self.reset
			sleep(0.1)
			if not ready:
				raise e
			else:
				return
		cap = camera.file_get(capinfo.folder, capinfo.name, gp.GP_FILE_TYPE_NORMAL)
		lastImage = BIO(cap.get_data_and_size()).getvalue()
		for client in imageClients:
			client.set()
		self.purge_events()
	
	def purge_events(self, timeout = 100):
		global ready, camera
		if not ready:
			return
		code = 0
		count = 0
		while code != 1:
			try:
				code, ed = camera.wait_for_event(timeout)
				logging.debug(ed)
			except gp.GPhoto2Error as e:
				sleep(0.1)
				count += 1
				if count > 10:
					break
			except Exception as e:
				logging.error(e)
				raise e
		while code != 1:
			logging.debug(ed)
			code, ed = camera.wait_for_event(timeout)
	
	def release(self):
		global ready, camera, bus, address, config, settings
		if camera is not None:
			camera.exit()
			logging.info('Camera Released')
			camera = None
			bus = None
			address = None
			config = None
			settings = None
			ready = False

	def ready(self):
		global ready
		return ready
	
	def getURL(self):
		global streamUrl
		return streamUrl
	
	def setURL(self, url):
		global streamUrl
		streamUrl = url
		
	def isPreviewing(self):
		global previewing
		return previewing
	
	def stopPreviewing(self):
		global previewing
		previewing = False
		
	def get_settings(self):
		global settings
		return settings
# if __name__ == '__main__':
	# # Add functions necessary for service HERE
	# import signal
	# import socket
	# from select import select
	# from sys import exit as EXIT
	# import sys
	
	# class StopGuard:
		# kill_now = False
		# def __init__(self):
			# signal.signal(signal.SIGINT, self.exit_gracefully)
			# signal.signal(signal.SIGTERM, self.exit_gracefully)

		# def exit_gracefully(self,signum, frame):
			# self.kill_now = True
			# raise SystemExit('StopGuard intercepted TERM signal')

	# stoppingGuard = StopGuard()
	
	
	
	# loglevel = logging.WARNING
	# for arg in sys.argv:
		# if 'log-level' in arg:
			# pass
	# camera = jwbcamera()