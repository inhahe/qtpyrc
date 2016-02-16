import sys 

def newmodule( modname ): 
	import null_module 
	sys.modules[modname] = sys.modules['null_module'] 
	sys.modules[modname].__name__ = modname  
	del sys.modules['null_module'] 
	del null_module 
	return sys.modules[modname]  

def importmodule( filename, modname ): 
	module = newmodule( modname ) 
	execfile( filename, module.__dict__, module.__dict__  )  
	return module  

def caller(): 
	import sys 
	try: 
		1 + ''		# make an error happen 
	except:			# and return the caller's caller's frame 
		return sys.exc_traceback.tb_frame.f_back.f_back 

def ImportModule( filename, modname ): 
	newmodule = importmodule( filename, modname ) 
	frame = caller()	# get the caller's frame 
	frame.f_globals[modname] = newmodule	# and enter name in dict 