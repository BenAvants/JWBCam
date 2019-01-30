#!/usr/bin/python3

import socket
import json
from time import perf_counter as tic, sleep
from threading import Thread, Event
from collections import OrderedDict

class BusyError(Exception):
	def __init__(self,message):
		self.message = message

class ConnectionError(Exception):
	def __init__(self,message):
		self.message = message

class jwbstepper():
	commandList = {'connect'			:'none',
					'close'				:'none',
					'is_connected'		:'none',
					'enable'			:'none',
					'disable'			:'none',
					'stop'				:'none',
					'hardstop'			:'none',
					'getstate'			:'none',
					'step'				:'steps=(+/-)int',
					'stepTo'			:'position=(+/-)int',
					'move'				:'distance=(+/-)float',
					'moveTo'			:'position=(+/-)float',
					'runfor'			:'seconds=(+)float',
					'kill'				:'none',
					'settings'			:'none',
					'settingsList'		:'none',
					'commands'			:'none',
					'commandsList'		:'none'}

	def __init__(self):
		self.__sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		try:
			self.__sock.connect('\0JWBCamPIGPIO.sock')
		except Exception as e:
			print(e)
			self.__sock = None
			self.__connected = False
			self.__settings = {}
		else:
			try:
				self.__sock.settimeout(0.1)
				self.__sock.send(b'settings')
				encoded = self.__sock.recv(1024)
			except Exception as e:
				print(e)
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				self.__settings = {}
			else:
				self.__connected = True
				self.__settings = json.loads(encoded.decode('utf-8'), object_pairs_hook=OrderedDict)
				self.__lastUpdate = tic()
				self.__newMove = Event()
				self.__poller = Thread(target = jwbstepper.__pollSettings, args=(self,))
				self.__poller.start()
	
	def __getattr__(self, name):
		if name in self.__settings:
			if self.__connected:
				try:
					self.__sock.send(name.encode('utf-8'))
					val = self.__sock.recv(1024)
					if not val:
						self.__sock.close()
						self.__sock = None
						self.__connected = False
						val = self.__settings[name]
					else:
						val = val.decode('utf-8')
				except:
					val = self.__settings[name]
				return val
			else:
				return self.__settings[name]
		else:
			raise AttributeError(name)
	
	def __setattr__(self, name, value):
		if not '_jwbstepper__settings' in self.__dict__: # Only true before __settings dict is initialized
			return object.__setattr__(self, name, value)
		if name in self.__settings: # If the attribute is found in the settings dict, attempt sending the new value to the GPIO process
			if not self.__connected: # Not connected, cannot set attribute - raise AttributeError - Settings are read-only once connection is lost
				raise AttributeError(name)
			msg = name + str(value)
			try: # Try sending message to change setting
				self.__sock.send(msg.encode('utf-8'))
				encoded = self.__sock.recv(1024)
			except: # Send or receive failure implies connection failure - raise AttributeError - Settings are read-only once connection is lost
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise AttributeError(name)
			else: # Communication was successful, check for invalid message - raise ValueError if setting invalid
				if encoded == b'invalid':
					raise ValueError(str(value) + ' is invalid for ' + name)
				if encoded == b'busy':
					raise BusyError('busy')
				self.__settings[name] = value # Complete success, store new value locally
		else: # Attribute isn't a current setting, treat normally
			return object.__setattr__(self, name, value)
	
	def __pollSettings(stepper):
		while stepper.__lastUpdate:
			if stepper.__newMove.wait(0.01):
				try:
					stepper.__sock.send(b'settings')
					encoded = stepper.__sock.recv(1024)
				except Exception as e:
					stepper.__connected = False
					stepper.__sock.close()
					stepper.__sock = None
					
					stepper.__settings = {}
				else:
					stepper.__settings = json.loads(encoded.decode('utf-8'), object_pairs_hook=OrderedDict)
					return stepper.__settings
	
	def connect(self):
		if self.__connected:
			return True
		self.__sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		try:
			self.__sock.connect('\0JWBCamPIGPIO.sock')
		except:
			self.__sock = None
			self.__connected = False
			self.__settings = {}
			return False
		else:
			try:
				self.__sock.send(b'settings')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				self.__settings = {}
				return False
			else:
				self.__connected = True
				self.__settings = {}
				settings = json.loads(encoded.decode('utf-8'))
				for setting in settings:
					if hasattr(self,setting):
						delattr(self,setting)
				self.__settings = settings
				self.__lastUpdate = tic()
				self.__newMove.clear()
				self.__poller = Thread(target=__pollSettings, args=(self,))
				self.__poller.start()
				return True
	
	def close(self):
		self.__lastUpdate = None
		self.__newMove.set()
		self.__poller.join(1)
		del self.__poller
		self.__sock.close()
		self.__sock = None
		self.__connected = False
	
	def is_connected(self):
		return self.__connected
	
	def enable(self):
		if self.__connected:
			try:
				self.__sock.send(b'enable')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def disable(self):
		if self.__connected:
			try:
				self.__sock.send(b'disable')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def stop(self):
		if self.__connected:
			try:
				self.__sock.send(b'stop')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def hardstop(self):
		if self.__connected:
			try:
				self.__sock.send(b'hard')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def flipDir(self):
		if self.__connected:
			try:
				self.__sock.send(b'flipdir')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def getstate(self):
		if self.__connected:
			try:
				self.__sock.send(b'state')
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
			else:
				return encoded.decode('utf-8')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def step(self, steps):
		if self.__connected:
			try:
				steps = str(steps)
				test = int(steps)
			except:
				raise ValueError('steps must be an int or string representation of an int')
			try:
				msg = 'step' + steps
				self.__sock.send(msg.encode('utf-8'))
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
			else:
				if encoded == b'busy':
					raise BusyError('busy')
				self.__newMove.set()
				return encoded.decode('utf-8')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')

	def stepTo(self, position):
		if self.__connected:
			try:
				position = str(position)
				test = int(position)
			except:
				raise ValueError('position must be an int or string representation of an int')
			try:
				msg = 'stepTo' + position
				self.__sock.send(msg.encode('utf-8'))
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
			else:
				if encoded == b'busy':
					raise BusyError('busy')
				self.__newMove.set()
				return encoded.decode('utf-8')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def move(self, distance):
		if self.__connected:
			try:
				distance = str(distance)
				test = float(distance)
			except:
				raise ValueError('distance must be numerical or a string representation of a number')
			try:
				msg = 'move' + distance
				self.__sock.send(msg.encode('utf-8'))
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
			else:
				if encoded == b'busy':
					raise BusyError('busy')
				self.__newMove.set()
				return encoded.decode('utf-8')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def moveTo(self,position):
		if self.__connected:
			try:
				position = str(position)
				test = float(position)
			except:
				raise ValueError('position must be a float or string representation of a float')
			try:
				msg = 'moveTo' + position
				self.__sock.send(msg.encode('utf-8'))
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
			else:
				if encoded == b'busy':
					raise BusyError('busy')
				self.__newMove.set()
				return encoded.decode('utf-8')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def runfor(self,seconds = float('inf')):
		if self.__connected:
			try:
				seconds = str(seconds)
				test = float(seconds)
			except:
				raise ValueError('seconds must be numerical or a string representation of a number')
			try:
				msg = 'run' + seconds
				self.__sock.send(msg.encode('utf-8'))
				encoded = self.__sock.recv(1024)
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
			else:
				if encoded == b'busy':
					raise BusyError('busy')
				self.__newMove.set()
				return encoded.decode('utf-8')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def stopAndWait(self):
		if not self.__connected:
			raise ConnectionError('JWBCamPIGPIO not connected')
		if not self.getstate() in ('Disabled','Stopped'):
			self.stop()
		while not self.getstate() in ('Disabled','Stopped'):
			sleep(0.025)
	
	def kill(self):
		if self.__connected:
			try:
				self.__sock.send(b'terminate')
			except:
				self.__sock.close()
				self.__sock = None
				self.__connected = False
				raise ConnectionError('JWBCamPIGPIO not connected')
		else:
			raise ConnectionError('JWBCamPIGPIO not connected')
	
	def settings(self):
		for setting in self.__settings:
			print(setting.ljust(20) + ': ' + str(self.__settings[setting]))
	
	def settingsList(self):
		return self.__settings.keys()
	
	def settingsVals(self):
		if not self.__connected:
			return self.__settings
		try:
			self.__sock.send(b'settings')
			encoded = self.__sock.recv(1024)
		except Exception as e:
			print(e)
			self.__sock.close()
			self.__sock = None
			self.__connected = False
			self.__settings = {}
		else:
			self.__settings = json.loads(encoded.decode('utf-8'), object_pairs_hook=OrderedDict)
			return self.__settings
	
	def commands(self):
		for command in self.commandList:
			print(command.ljust(15) + ': ' + self.commandList[command])
	
	def commandsList(self):
		return self.commandList.keys()