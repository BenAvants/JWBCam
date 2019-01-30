



from time import time, sleep
from datetime import datetime
from sh import gphoto2 as gp
import signal, os, subprocess

cameraDirectory = '/store_00020001/DCIM/100CANON'

clearCommand = ['--folder', cameraDirectory,'-R', '--delete-all-files']

triggerCommand = ['--trigger-capture']

downloadCommand = ['--get-all-files']

def killGP2Proc():
	p = subprocess.Popen(['ps', '-A'], stdout=subprocess.PIPE)
	out, err = p.communicate()
	for line in out.splitlines():
		if b'gvfsd-gphoto2' in line:
			pid = int(line.split(None,1)[0]
			os.kill(pid, signal.SIGKILL)

def createSaveFolder():
	try:
		os.makedirs(save_location)
	except:
		print('Failed to create the new directory')
	os.chdir(save_location)

def captureImage():
	gp(triggerCommand)
	sleep(1)
	gp(downloadCommand)
	gp(clearCommand)

def renameFile(ID):
	





