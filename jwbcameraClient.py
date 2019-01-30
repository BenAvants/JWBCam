#!/usr/bin/python3

import json
from threading import Event
from multiprocessing import Event as mpEvent
from collections import OrderedDict
from io import BytesIO as BIO
from time import sleep
from weakref import WeakSet
import socket
from sys import byteorder as BO




class jwbcameraClient():
	settings = None
	lastImage = None
	imageClients = WeakSet()
	settingsClients = WeakSet()
	connected = False
	gpsock = None

	def __init__(self):
		self.gpsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		try:
			self.gpsock.connect('\0JWBCamGpPi.pe')
		except Exception as e:
			self.gpsock = None
			self.connected = False
			self.settings = None
		else:
			try:
				self.gpsock.settimeout(0.1)
				self.gpsock.send(b'getSettings')
				count = int.from_bytes(self.gpsock.recv(2), BO)
				encoded = self.gpsock.recv(count)
			except Exception as e:
				print(e)
				self.connected = False
				self.gpsock.close()
				self.gpsock = None
				self.settings = None
			else:
				self.settings = json.loads(encoded.decode('utf-8'), object_pairs_hook=OrderedDict)
				