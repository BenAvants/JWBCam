#!/usr/bin/python3

# Change the hostname in /etc/hostname and in /etc/hosts to set the network name

import logging
from flask import Flask, render_template, redirect, url_for, session, request, Response, flash
from argon2 import PasswordHasher
import json
import os
import subprocess
from jwbstepper import jwbstepper, BusyError, ConnectionError
# from jwbcamera import jwbcamera
from jwbcameraClient import jwbcameraClient
from time import sleep
from collections import OrderedDict
from threading import Thread, Event
from multiprocessing import Event as mpEvent


global mainStyle, credentials, preferences, gpio, ph
global stepper, stepperlocals, stepperEvent, camera, cameralocals, cameraEvent
global locked, stopPreview, updateEvent

app = Flask(__name__)
app.secret_key = b'%u*\xb1\\\xd0\x94\xde7\x8c\xb8\xda&\xd0\xfbu\xc0\xb1\x0e<\xbd\x10~\xc3'

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
else:
	app.logger.setLevel('debug')

updateEvent = mpEvent()
locked = False
stopPreview = False

stepper = jwbstepper()
stepperlocals = {'runLimit':10, 'stepSize':1}
# stepSize = 1 # the distance in mm to step per step command
# runLimit = 10 # the timeout in seconds for a stepper run command - prevents infinite running
settingNames = tuple(stepper.settingsList())
commandNames = tuple(stepper.commandsList())

# camera = jwbcamera(updateEvent=updateEvent,logLevel=app.logger.level)
canera = jwbcameraClient(imageEvent=updateEvent)
cameralocals = {}

#camera = {'streamUrl':'http://88.53.197.250/axis-cgi/mjpg/video.cgi?resolution=320x240','settings':{}}
#cameralocals = {}

ph = PasswordHasher()

ip = subprocess.check_output(['hostname','-I']).decode('utf-8').strip()
iplink = 'http://' + ip + '/'

defaultUser = {'name':'Guest',
				'isAdmin':False,
				'isGuest':True,
				'style':'Slate',
				'LogLink':'/login/',
				'LogAction':'log in',
				'refresh':False,
				'RefreshRate':'2',
				'notes':''
				}

if os.path.isfile('pwh.txt'):
	with open('pwh.txt','r') as f:
		credentials = json.load(f)
	if os.path.isfile('prefs.txt'):
		with open('prefs.txt','r') as f:
			preferences = json.load(f)
	else:
		preferences = {}
		for name in credentials:
			preferences[name] = defaultUser
		with open('prefs.txt','r') as f:
			json.dump(preferences,f)
else:
	credentials = {'Admin':'$argon2i$v=19$m=512,t=2,p=2$30+9iM0y+pANr+Ob3zlaaQ$KpCuOuN1o3/RdCClwMqI6g'}
	admin = defaultUser.copy()
	admin['name'] = 'Admin'
	admin['isAdmin'] = True
	admin['isGuest'] = False
	admin['LogLink'] = '/logout/'
	admin['LogAction'] = 'logout'
	preferences = {'Admin':admin}
	with open('pwh.txt','w') as f:
		json.dump(credentials,f)
	with open('prefs.txt','w') as f:
		json.dump(preferences,f)

mainStyle = 'Slate'

@app.route('/')
def index():
	if 'username' in session:
		user = preferences[session['username']]
	else:
		user = defaultUser
	return render_template('index.html',HText='JWB-Cam',IP=iplink,user=user)

@app.route('/login/', methods=['GET','POST'])
def login():
	if request.method == 'POST':
		if request.form['username'] in credentials:
			try:
				ph.verify(credentials[request.form['username']],request.form['password'] + '_' + request.form['username'])
			except:
				return redirect(url_for('index'))
			session['username'] = request.form['username']
			return redirect(url_for('index'))
	return render_template('login.html',user=defaultUser,HText='JWB-Cam',IP=iplink)

@app.route('/user/', methods=['GET', 'POST'])
def userpage():
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if user['isGuest']:
		return redirect(url_for('index'))
	
	
@app.route('/logout/')
def logout():
	if 'username' in session:
		del session['username']
	return redirect(url_for('index'))

@app.route('/manage/')
def manage():
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	refresh = {'enable':False, 'rate':user['RefreshRate']}
	return render_template('manage.html',HText='JWB-Cam',IP=iplink,user=user,preferences=preferences)

@app.route('/manage/addUser', methods=['GET','POST'])
def addUser():
	global credentials, preferences
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if request.method == 'GET':
		user = preferences[session['username']]
		return render_template('addUser.html',HText='JWB-Cam',IP=iplink,user=user)
	else:
		credentials.update({request.form['username']:ph.hash(request.form['password'] + '_' + request.form['username'])})
		newuser = defaultUser.copy()
		newuser.update({'name':request.form['username'],'notes':request.form['notes']})
		newuser['LogLink'] = '/logout/'
		newuser['LogAction'] = 'logout'
		newuser['isAdmin'] = 'isadmin' in request.form
		newuser['isGuest'] = False
		preferences[request.form['username']] = newuser
		os.replace('pwh.txt','pwh.txt.bak')
		os.replace('prefs.txt','prefs.txt.bak')
		with open('pwh.txt','w') as f:
			json.dump(credentials,f)
		with open('prefs.txt','w') as f:
			json.dump(preferences,f)
		return redirect(url_for('manage'))

@app.route('/manage/users/<name>')
def userdetails(name):
	global preferences, credentials
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	return str(preferences[name])

@app.route('/stepper/')
def stepperroot():
	global stepperlocals, stepper
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	return render_template('stepper.html',HText='JWB-Cam',IP=iplink,user=user,
							stepper=stepper,stepperlocals=stepperlocals)

@app.route('/stepper/command/<command>', methods=['GET','POST'])
def steppercom(command=None):
	global stepper, stepperlocals, locked
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if locked:
		commandCode = 'locked'
	else:
		commandCode = 'failure'
		try:
			if command == 'stop':
				stepper.stop()
			elif command == 'runUp':
				stepper.stopAndWait()
				stepper.runfor(stepperlocals['runLimit'])
			elif command == 'stepUp':
				stepper.stopAndWait()
				stepper.move(stepperlocals['stepSize'])
			elif command == 'stepDown':
				stepper.stopAndWait()
				stepper.move(-stepperlocals['stepSize'])
			elif command == 'runDown':
				stepper.stopAndWait()
				stepper.runfor(-stepperlocals['runLimit'])
			elif command == 'goHome':
				stepper.stopAndWait()
				stepper.stepTo(0)
			elif command == 'flipDir':
				stepper.flipDir()
			elif command == 'hardstop':
				stepper.hardstop()
			elif command == 'enable':
				stepper.enable()
			elif command == 'disable':
				stepper.disable()
			elif command == 'stepSize':
				stepperlocals['stepSize'] = float(request.form['stepSize'])
			elif command == 'runLimit':
				stepperlocals['runLimit'] = float(request.form['runLimit'])
			elif command in commandNames:
				result = getattr(stepper, command)()
			elif (command.startswith(settingNames) and 
					command.endswith(settingNames) and 
					request.method == 'POST'):
				setattr(stepper,command,request.form[command])
		except BusyError:
			commandCode = 'busy'
			sleep(0.025)
		except ConnectionError:
			logging.warn('No Stepper Connected')
			commandCode = 'failure'
		except ValueError as e:
			logging.error(e)
			commandCode = 'failure'
		except Exception as e:
			logging.error(e)
			commandCode = 'failure'
		else:
			commandCode = 'success'
	if request.method == 'GET':
		return redirect(url_for('stepperroot'))
	else:
		# print(request.form)
		if 'scripted' in request.form:
			return commandCode
		else:
			return redirect(url_for('stepperroot'))

@app.route('/camera/')
def cameraroot():
	global cameralocals, camera
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	camera.setURL(url_for('camera_image_stream'))
	if not camera.ready():
		camera.reset()
		if not camera.ready():
			logging.error('Camera Error')
			return redirect(url_for('index'))
	return render_template('camera.html',HText='JWB-Cam',IP=iplink,user=user,
							camera=camera,cameralocals=cameralocals)

@app.route('/camera/command/<command>', methods=['GET','POST'])
def cameracom(command=None):
	global cameralocals, camera, locked
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if locked:
		commandCode = 'locked'
	else:
		commandCode = 'failure'
		try:
			if camera is None:
				logging.error('Camera is None')
			if camera.get_settings() is None:
				logging.error('Settings are None')
			ckeys = camera.get_settings().keys()
			if command in camera.get_settings().keys() and request.method == 'POST':
				camera.changeSetting(command,request.form[command])
			else:
				logging.info(command)
				logging.debug(request.form[command])
		except ValueError as e:
			logging.error('Setting change failed')
			logging.error(e)
			commandCode = 'failure'
		except Exception as e:
			logging.error('Changing settings unhandled exception')
			logging.error(e)
			commandCode = 'failure'
		else:
			commandCode = 'success'
	if request.method == 'GET':
		return redirect(url_for('cameraroot'))
	else:
		if 'scripted' in request.form:
			return commandCode
		else:
			return redirect(url_for('cameraroot'))

@app.route('/camera/reset/')
def camerareset():
	global cameralocals, camera, stopPreview
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if camera.isPreviewing():
		camera.stopPreviewing()
	stopPreview = True
	sleep(.1)
	camera.reset()
	return redirect(url_for('index'))

@app.route('/camera/release/')
def camerarelease():
	global camera
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	camera.release()
	return redirect(url_for('index'))

@app.route('/camera/stream/images/')
def camera_image_stream():
	return Response(cameraImageStream(),mimetype='multipart/x-mixed-replace; boundary=frame')

def cameraImageStream():
	global camera, stopPreview
	newImageEvent = mpEvent()
	stopPreview = False
	camera.registerClient(newImageEvent)
	while not stopPreview:
		if newImageEvent.wait(0.1):
			yield (b'--frame\r\n'
					b'Content-Type: image/jpeg\r\n\r\n' + camera.lastImage + b'\r\n')

@app.route('/camera/stream/settings/')
def camera_settings_stream():
	global camera
	return Response(cameraSettingsStream(camera),mimetype="text/event-stream")

def cameraSettingsStream(camera):
	if not camera.ready():
		logging.warning('camera not ready')
		yield 'data: {} \n\n'
		return
	oldvals = []
	newvals = []
	for info in camera.get_settings().values():
		oldvals.append(info[2])
		newvals.append(info[2])
	while camera.ready():
		for ind, info in enumerate(camera.get_settings().values()):
			newvals[ind] = info[2]
		if newvals != oldvals:
			oldvals = newvals
			yield 'data: ' + json.dumps(camera.get_settings()) + '\n\n'
		sleep(0.5)
		

@app.route('/camera/streamwindow/')
def camerapreviewwindow():
	global cameralocals, camera
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	camera.setURL(url_for('camera_image_stream'))
	return render_template('preview.html',HText='JWB-Cam',IP=iplink,user=user,
							camera=camera,cameralocals=cameralocals)


@app.route('/camera/streampreview/')
def camerapreview():
	global cameralocals, camera
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	camera.setURL(url_for('camera_image_stream'))
	if not camera.ready():
		camera.initialize()
		if not camera.ready():
			logging.warning('Camera Error')
			return redirect(url_for('index'))
	camera.preview()
	return redirect(url_for('index'))
	# return render_template('preview.html',HText='JWB-Cam',IP=iplink,user=user,
							# camera=camera,cameralocals=cameralocals)


@app.route('/camera/stoppreview/')
def camerarstoppreview():
	global cameralocals, camera, stopPreview
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if camera.isPreviewing():
		camera.stopPreviewing()
		sleep(.25)
		camera.purge_events()
		#camera.capture_image()
	return redirect(url_for('index'))


@app.route('/camera/initialize/')
def camerarinitialize():
	global cameralocals, camera, stopPreview
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if not camera.ready():
		camera.initialize()
	return redirect(url_for('index'))


@app.route('/camera/captureimage/')
def camerarcaptureimage():
	global cameralocals, camera, stopPreview
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	if camera.isPreviewing():
		camera.stopPreviewing()
		stopPreview = True
		sleep(.1)
		camera.purge_events()
	camera.capture_image()
	return redirect(url_for('index'))


@app.route('/camera/settings/')
def camerasettings():
	global cameralocals, camera
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if not user['isAdmin']:
		return redirect(url_for('index'))
	camera.setURL(url_for('camera_image_stream'))
	if not camera.ready():
		logging.warning('Camera not ready')
		return redirect(url_for('index'))
		# camera.reset()
		# camera.initialize()
		# if not camera.ready():
			# logging.warning('Camera Error')
			# return redirect(url_for('index'))
	return render_template('camset.html',HText='JWB-Cam',IP=iplink,user=user,
							camera=camera,cameralocals=cameralocals)


@app.route('/stepper/stream/')
def stepper_stream():
	return Response(stepperStream(),mimetype="text/event-stream")

def stepperStream():
	global stepper
	vals = stepper.settingsVals()
	while stepper.is_connected():
		sleep(0.2)
		newVals = stepper.settingsVals()
		if newVals != vals:
			vals = newVals
			yield 'data: ' + json.dumps(vals) + '\n\n'

@app.route('/JWBCam/status/')
def status_updates():
	return Response(statusStream(),mimetype="text/event-stream")

def statusStream():
	global camera, stepper, updateEvent
	return redirect(url_for('index'))

@app.route('/style/<style>')
def setStyle(style):
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	list = ['Anger',
			'Sunshine',
			'Slate']
	if style in list:
		user['style'] = style
	return redirect(url_for('index'))

@app.route('/refreshToggle/', methods=['POST'])
def refreshToggle():
	if not 'username' in session:
		return redirect(url_for('index'))
	user = preferences[session['username']]
	if user['refresh']:
		user['refresh'] = False
	else:
		user['refresh'] = True
	return redirect(url_for('index'))

if __name__ == '__main__':
	app.run(debug=True, threaded=True, host='0.0.0.0', port=5000)
	
