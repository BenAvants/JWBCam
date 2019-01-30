#!/usr/bin/python3

## Desired functionality
# Lock / Unlock Buttons
# Respond to Buttons

import logging
import signal
import pigpio
import socket
from collections import OrderedDict
import json
import math
from select import select
from threading import Thread, Event
from time import time, sleep
from sys import exit as EXIT

class StopGuard:
	kill_now = False
	def __init__(self):
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

	def exit_gracefully(self,signum, frame):
		self.kill_now = True
		raise SystemExit('StopGuard intercepted TERM signal')

stoppingGuard = StopGuard()

class Settings():
	def __init__(self, *args, **kwargs):
		self._attrs = OrderedDict(*args, **kwargs)
	
	def __getattr__(self, name):
		try:
			return self._attrs[name]
		except KeyError:
			raise AttributeError(name)
	
	def __setattr__(self, name, value):
		if name == '_attrs':
			return super(Settings, self).__setattr__(name, value)
		self._attrs[name] = value
	
	def list(self):
		return self._attrs

global running, stopFlag, pi, stepperState, stepperAwake, settings, movedir, stepsToTake

running = True
stopFlag = False
pi = None
stepperState = 0
stepperAwake = Event()
settings = Settings()
# message = ''
movedir = 1
dir2pin = {1:1,-1:0}
stepsToTake = 0
servesock = None

settings.Stepper_Position = 0
settings.Direction = 'CW'
settings.Velocity = 9.42477 # Maximum Velocity in radians per second - DEFAULT = 3 RPS / 2
settings.Acceleration = 50 # Acceleration (+) in radians per second per second
settings.Deceleration = -100 # Deceleration (-) in radians per second per second
settings.Auto_Disable = True
settings.Microstep = 32
settings.Screw_Lead = 8 # travel per rotation in mm
settings.Base_Step_Angle = 0.0314159
settings.Steps_Per_Rotation = 200
settings.Version = '1.1.0_20180112'
# settings.Wavelet_Length = 25000 # Time in microseconds for each PWM wavelet 
#  - should be less than 50 ms to feel responsive

fullSettingsList = {'Acceleration':('integer','bounded',50,(0.1,500)),
					'Deceleration':('integer','bounded',-100,(-500,-0.1)),
					'Velocity':('float','bounded',9.42477,(0.1,100)),
					'Direction':('string','enumerated','CW',('CW','CCW','Toggle')),
					'Base_Step_Angle':('float','bounded',0.0314159,(0.001,1.570795)),
					'Screw_Lead':('float','bounded',8,(0.2,25.4)),
					'Steps_Per_Rotation':('integer','bounded',200,(4,1200)),
					'Stepper_Position':('integer','infinite',0,(-float('inf'),float('inf'))),
					'Auto_Disable':('boolean','enumerated',True,(False,True)),
					'Version':('string','constant',settings.Version,(None,None)),
					'Microstep':('integer','list',16,(1,2,4,8,16,32))}

C0 = 53584
enabled = False

lastWave = None
waveBeforeLast = None
moveStartTime = 0
moveMaxTime = 10
infinity = float('inf')
decelFactor = -settings.Deceleration/settings.Acceleration # precalculated dec/acc factor

stepperStates = {0 : 'Disabled',
				 1 : 'Stopped',
				 2 : 'Accelerating',
				 3 : 'Moving',
				 4 : 'Decelerating'}

stepperPins = {'Direction'	: 15,
			   'Step'		: 18,
			   'Sleep'		: 27,
			   'Reset'		: 23,
			   'MS3'		: 24,
			   'MS2'		: 10,
			   'MS1'		: 25,
			   'Enable'		: 8}

ustepStatePins = {1	: (0,0,0),
				  2	: (1,0,0),
				  4	: (0,1,0),
				  8	: (1,1,0),
				  16	: (0,0,1),
				  32	: (1,1,1)}

ustepStateMults = {1	: 1,
				   2	: 0.5,
				   4	: 0.25,
				   8	: 0.125,
				   16	: 0.0625,
				   32	: 0.03125}

def compC0():
	# 0.676*1000000*sqrt(2*baseStepAngle*ustepStateMults[ustepState]/accel))
	global C0, ustepStateMults, settings
	accel = settings.Acceleration
	sAngle = settings.Base_Step_Angle
	C0 = 676000*math.sqrt(2*sAngle*ustepStateMults[settings.Microstep]/accel)

def recomp():
	global pi
	pi.wave_clear()
	compC0()

def enable():
	global pi, stepperPins, stepperState, enabled
	pi.write(stepperPins['Enable'],0)
	enabled = True
	if stepperState == 0:
		stepperState = 1

def disable():
	global pi, stepperPins, enabled, stepperState
	pi.write(stepperPins['Enable'],1)
	enabled = False
	stepperState = 0

def setdir(direction='Toggle'):
	global pi, movedir, settings, dir2pin
	val = -1
	if direction.startswith('CW'):
		movedir = 1
		val = dir2pin[movedir]
		settings.Direction = 'CW'
	elif direction.startswith('CCW'):
		movedir = -1
		val = dir2pin[movedir]
		settings.Direction = 'CCW'
	else:
		if movedir == 1:
			movedir = -1
			val = dir2pin[movedir]
			settings.Direction = 'CCW'
		else:
			movedir = 1
			val = dir2pin[movedir]
			settings.Direction = 'CW'
	if val > -1:
		pi.write(stepperPins['Direction'],val)
		return 'success'
	else:
		print('Direction Set Fail: ' + direction)
		return 'invalid'

def stepSize(newState=16):
	global pi, settings, ustepStatePins, stepperPins
	try:
		levels = ustepStatePins[newState]
	except:
		return
	pi.write(stepperPins['MS1'],levels[0])
	pi.write(stepperPins['MS2'],levels[1])
	pi.write(stepperPins['MS3'],levels[2])
	settings.Microstep = newState
	compC0()

def dist2step(dist = 1):
	global settings
	spr = settings.Steps_Per_Rotation
	ustepm = ustepStateMults[settings.Microstep]
	rawSteps = round(dist * spr / (ustepm * settings.Screw_Lead))
	return (rawSteps,step2dist(rawSteps))

def step2dist(steps = 1):
	global settings
	return steps * ustepStateMults[settings.Microstep] * settings.Screw_Lead / settings.Steps_Per_Rotation

def startPigpio():
	global pi, stepperPins, settings
	pi = pigpio.pi()
	for pin in stepperPins:
		if stepperPins[pin]:
			pi.set_mode(stepperPins[pin],pigpio.OUTPUT)
			if pin in ('Enable','Sleep','Reset'):
			  level = 1
			else:
				level = 0
			pi.write(stepperPins[pin],level)
	stepSize(settings.Microstep)

def stopPigpio():
	global pi, enabled
	if pi:
		if enabled:
			disable()
		pi.stop()
		pi = None

def stepperControl():
	global pi, lastWave, waveBeforeLast, C0, decelFactor, stepsToTake, ustepStateMults, movedir
	global stepperPins, stopFlag, stepperState, moveStartTime, moveMaxTime, settings, stepperAwake
	stepPin = stepperPins['Step']
	notx = pigpio.NO_TX_WAVE
	nowave = pigpio.WAVE_NOT_FOUND
	while not stopFlag:
		if stepperState < 2:
			stepperAwake.clear()
			if not stepperAwake.wait(0.01):
				continue
		sigma = 0.736*settings.Base_Step_Angle*ustepStateMults[settings.Microstep]
		vel2 = settings.Velocity*settings.Velocity
		accel = settings.Acceleration
		decel = settings.Deceleration
		accelFinished = math.floor(vel2 / (sigma*accel))
		decelSteps = -math.floor(vel2 / (sigma*decel))
		if stepsToTake < float('inf'):
			decelStarted = stepsToTake - decelSteps
			if decelStarted < accelFinished:
				accelFinished = int(-stepsToTake*decel / (accel - decel))
				decelStarted = stepsToTake - accelFinished
				decelSteps = stepsToTake - decelStarted
		else:
			decelStarted = float('inf')
		startPos = settings.Stepper_Position
		n = int(0)
		C = C0
		lastWave = None
		waveBeforeLast = None
		#print('Accelerate ' + str(accelFinished) + ' steps\nDecelerate ' + str(decelSteps) + ' steps')
		moveStartTime = time()
		while stepperState > 1 and not stopFlag:
			wave = []
			pulses = math.ceil(25000/C)
			for ii in range(pulses):
				wave.append(pigpio.pulse(1<<stepPin,0,5))
				wave.append(pigpio.pulse(0,1<<stepPin,round(C-5)))
				n += 1
				if n >= stepsToTake:
					break
				if n <= accelFinished:
					C = C - (2*C * (accelFinished-n) / ((4*n+1)*(accelFinished)))
				elif n >= decelStarted:
					C = C - (2*C * (n-decelStarted) / ((4*(n-stepsToTake)+1)*(stepsToTake-decelStarted-1)))
			try:
				pi.wave_add_generic(wave)
			except:
				print(wave)
				raise
			try:
				newWave = pi.wave_create()
			except:
				pi.wave_clear()
				pi.wave_add_generic(wave)
				newWave = pi.wave_create()
				lastWave = None
				waveBeforeLast = None
			CW = pi.wave_tx_at() # current wave ID
			while CW != notx and CW != nowave and CW != lastWave and not stopFlag:
				sleep(0.001)
				CW = pi.wave_tx_at()
			if not stopFlag:
				blocks = pi.wave_send_using_mode(newWave,pigpio.WAVE_MODE_ONE_SHOT_SYNC)
				settings.Stepper_Position = startPos + (movedir * n)
			if waveBeforeLast:
				try:
					pi.wave_delete(waveBeforeLast)
				except:
					print('Wave Delete Failed')
					pass
			waveBeforeLast = lastWave
			lastWave = newWave
			if n > accelFinished and stepperState == 2:
				stepperState = 3
			if n >= decelStarted and stepperState == 3:
				stepperState = 4
			if time() - moveStartTime >= moveMaxTime and stepperState < 4:
				stepperState = 4
			if stepperState == 4 and decelStarted > n:
				accelFinished = 0
				decelStarted = n
				stepsToTake = n + min(n/decelFactor,decelSteps)
			if n >= stepsToTake:
				stepperState = 1
		#print('Stopping')
		# settings.Stepper_Position = settings.Stepper_Position + (movedir * n)
		while not stopFlag and pi.wave_tx_busy():
			sleep(0.001)
		#print('Took ' + str(n) + ' steps')
		if waveBeforeLast:
			pi.wave_delete(waveBeforeLast)
		if lastWave:
			pi.wave_delete(lastWave)
		if settings.Auto_Disable and stepperState == 1:
			disable()

def move(movesteps=float('inf'),movetime=10):
	global stepsToTake, stepperState, stepperAwake, moveMaxTime
	if stepperState > 1:
		return 'busy'
	if stepperState == 0:
		enable()
	if movesteps < 0 or movetime < 0:
		setdir('CCW')
		movesteps = abs(movesteps)
		movetime = abs(movetime)
	else:
		setdir('CW')
	stepsToTake = movesteps
	moveMaxTime = movetime
	# print('Taking ' + str(stepsToTake) + ' steps over a maximum of ' + str(runtime) + ' seconds')
	stepperState = 2
	if not stepperAwake.is_set():
		stepperAwake.set()
	return 'success'

def parseMessage(message):
	global stepperState, stepperStates, decelFactor, stopFlag, running, settings, dir2pin
	if   message.startswith('stop'): # soft stop - stop from present state with deceleration
		if stepperState > 1:
			stepperState = 4
		return 'success'
	elif message.startswith('hard'): # hard stop - no more steps
		if stepperState > 1:
			stepperState = 1
		return 'success'
	elif message.startswith('disable'): # Disable the stepper motor driver
		disable()
		return 'success'
	elif message.startswith('state'): # set relative deceleration speed - positive scalar factor
		return stepperStates[stepperState]
	elif message.startswith('Direction'): # set movement direction
		if stepperState > 1:
			return 'busy'
		if len(message) == 9:
			setdir()
		elif message.endswith(('CW','CCW','Toggle')):
			return setdir(message[9:])
		else:
			return 'invalid'
	elif message.startswith('stepTo'):
		if stepperState > 1:
			return 'busy'
		if len(message) == 6:
			return 'invalid'
		try:
			position = int(message[6:])
		except ValueError:
			return 'invalid'
		steps = position-settings.Stepper_Position
		if abs(steps) < 2:
			return 'invalid'
		return move(steps,infinity)
	elif message.startswith('step'): # step a certain number of steps
		if stepperState > 1:
			return 'busy'
		if len(message) == 4:
			return 'invalid'
		try:
			steps = int(message[4:])
		except ValueError:
			return 'invalid'
		if abs(steps) < 2:
			return 'invalid'
		return move(steps,infinity)
	elif message.startswith('moveTo'):
		if stepperState > 1:
			return 'busy'
		if len(message) == 6:
			return 'invalid'
		try:
			destination = float(message[6:])
		except ValueError:
			return 'invalid'
		steps, dist = dist2step(destination - step2dist(settings.Stepper_Position))
		if abs(steps) < 2:
			return 'invalid'
		return move(steps,infinity)
	elif message.startswith('move'): # move a certain number distance
		if stepperState > 1:
			return 'busy'
		if len(message) == 4:
			return 'invalid'
		try:
			moveDist = float(message[4:])
		except ValueError:
			return 'invalid'
		steps, dist = dist2step(moveDist)
		if abs(steps) < 2:
			return 'invalid'
		return move(steps,infinity)
	elif message.startswith('run'):
		if stepperState > 1:
			return 'busy'
		if len(message) > 3:
			try:
				val = float(message[3:])
			except ValueError:
				return 'invalid'
		else:
			val = infinity
		return move(infinity,val)
	elif message.startswith('enable'): # Enable the stepper motor driver
		enable()
		return 'success'
	elif message.startswith('Microstep'): # set microstepping mode
		if len(message) == 9:
			return str(settings.Microstep)
		if stepperState > 1:
			return 'busy'
		if message.endswith(('1','2','4','8','16','32')):
			stepSize(int(message[9:]))
			return 'success'
		else:
			return 'invalid'
	elif message.startswith('Velocity'): # set maximum velocity - radians per second
		if len(message) == 8:
			return str(settings.Velocity)
		try:
			val = float(message[8:])
		except ValueError:
			return 'invalid'
		settings.Velocity = val
		compC0()
		return str(val)
	elif message.startswith('Acceleration'): # set acceleration - radians per second per second
		if len(message) == 12:
			return str(settings.Acceleration)
		try:
			val = float(message[12:])
		except ValueError:
			return 'invalid'
		if val <= 0:
			return 'invalid'
		settings.Acceleration = val
		decelFactor = -settings.Deceleration/settings.Acceleration
		compC0()
		return str(val)
	elif message.startswith('Deceleration'): # set relative deceleration speed - positive scalar factor
		if len(message) == 12:
			return str(settings.Deceleration)
		try:
			val = float(message[12:])
		except ValueError:
			return 'invalid'
		if val >= 0:
			return 'invalid'
		settings.Deceleration = val
		decelFactor = -settings.Deceleration/settings.Acceleration
		compC0()
		return str(val)
	elif message.startswith('Stepper_Position'): # set relative deceleration speed - positive scalar factor
		if stepperState > 1:
			return 'busy'
		if len(message) == 16:
			return str(settings.Stepper_Position)
		try:
			val = int(message[16:])
		except ValueError:
			return 'invalid'
		settings.Stepper_Position = val
		return str(val)
	elif message.startswith('Screw_Lead'): # set relative deceleration speed - positive scalar factor
		if stepperState > 1:
			return 'busy'
		if len(message) == 10:
			return str(settings.Screw_Lead)
		try:
			val = float(message[4:])
		except ValueError:
			return 'invalid'
		if val <= 0:
			return 'invalid'
		settings.Screw_Lead = val
		return str(val)
	elif message.startswith('Auto_Disable'): # Toggle controller automatically disabled after movement
		if len(message) == 12:
			settings.Auto_Disable = not settings.Auto_Disable
			return str(settings.Auto_Disable)
		if str(val) not in ['True','False']:
			return 'invalid'
		if str(val) == 'True':
			settings.Auto_Disable = True
		else:
			settings.Auto_Disable = False
		return str(settings.Auto_Disable)
	elif message.startswith('Version'): # Reply with running version of the code
		return settings.Version
	elif message.startswith('Base_Step_Angle'):
		if len(message) == 15:
			return settings.Base_Step_Angle
		try:
			val = float(message[15:])
		except ValueError:
			return 'invalid'
		settings.Base_Step_Angle = val
		settings.Steps_Per_Rotation = round(2 * math.pi / val)
		return str(val)
	elif message.startswith('Steps_Per_Rotation'):
		if len(message) == 18:
			return settings.Steps_Per_Rotation
		try:
			val = int(message[18:])
		except ValueError:
			return 'invalid'
		settings.Steps_Per_Rotation = val
		settings.Base_Step_Angle = 2 * math.pi / val
		return str(val)
	elif message.startswith('flipdir'):
		valP = dir2pin[1]
		valN = dir2pin[-1]
		dir2pin.update({1:valN,-1:valP})
		return 'success'
	elif message.startswith('fullsettings'):
		return json.dumps(fullSettingsList)
	elif message.startswith('settings'): # send a json encoding of external settings, 
		# does not indicate stepper state (enabled, and/or running)
		return json.dumps(settings.list())
	elif message.startswith('terminate'):
		stopFlag = True
		running = False
		return 'success'
	else:
		return 'invalid'

def comControl():
	global servesock, message, running
	try:
		messagesock, addr = servesock.accept()
		messagesock.settimeout(0.01)
	except:
		return
	connected = True
	while connected and running:
		try:
			chunk = messagesock.recv(64)
		except socket.timeout:
			continue
		except:
			raise
		else:
			if chunk:
				message = chunk.decode('utf-8')
				chunk = None
				reply = parseMessage(message)
				if reply != None:
					try:
						messagesock.send(reply.encode('utf-8'))
					except:
						pass
					message = None
					reply = None
			else:
				messagesock.close()
				connected = False
	if connected:
		messagesock.close()

if __name__ == '__main__':
	
	# Start PIGPIO
	try:
		startPigpio()
		pass
	except:
		print("JWBCamPIGPIO PIGPIO initialization failed")
		raise
  
  # Start Server Socket
	try:
		servesock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		servesock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
		servesock.bind('\0JWBCamPIGPIO.sock')
		servesock.listen(2)
	except:
		stopPigpio()
		print('JWBCamGPIO Socket initialization failed')
		raise
	
	stepperController = None
	# Run
	try:
		stepperController = Thread(target=stepperControl)
		stepperController.start()
		comControllers = []
		while running:
			r,w,e = select((servesock,),[],[],1)
			if r:
				t = Thread(target=comControl)
				comControllers.append(t)
				t.start()
				sleep(0.001)
			for controller in comControllers:
				if not controller.is_alive():
					controller.join(1)
					comControllers.remove(controller)
			
	except KeyboardInterrupt as ex:
		print(ex)
		print('Exiting via keyboard interrupt')
		raise
	except SystemExit as ex:
		print(ex)
		print('Exiting via system exit command')
		raise
	except pigpio.error as ex:
		print(ex)
		print('Exiting due to PIGPIO error')
		raise
	except Exception as ex:
		print(ex)
		print('JWBCamPIGPIO Unhandled Exception Encountered')
		raise
	finally:
		if servesock:
			servesock.close()
		stopFlag = True
		if not stepperAwake.is_set():
			stepperAwake.set()
		sleep(0.000002)
		running = False
		disable()
		stopPigpio()
		if stepperController:
			stepperController.join()
		for controller in comControllers:
			controller.join()
		EXIT(0)