import os
import sys

from urllib.parse import quote
import secrets
from datetime import datetime
import re
import io
import csv
import subprocess

from flask import Flask, Response, request, render_template, send_file
from flaskext.mysql import MySQL
from markupsafe import Markup

def get_config(name):
	with open(os.path.expanduser("~/sample_tracking/CONFIG")) as f:
		for line in f.readlines():
			if line.startswith('export '):
				line = line[len('export '):]
			s = line.split('=')
			if len(s) == 2 and s[0] == name:
				return s[1].strip("\"\n")
	return ""

domain = get_config("DOMAIN")
all_emails = list(filter(None,get_config("USERS").split(",")))
unrestricted_emails = list(filter(None,get_config("UNRESTRICTED_USERS").split(",")))
restricted_projects = list(filter(None,get_config("RESTRICTED_PROJECTS").split(",")))

with open(os.path.expanduser("~/public_html/.htaccess"), 'w') as f:
	f.write(f"""AuthType Shibboleth
ShibRequestSetting requireSession 1
Require shib-user {' '.join([x.split('<')[1].strip('<> ') for x in all_emails])}
ShibUseHeaders On
""")


application = Flask(__name__)
app = application
mysql = MySQL()
app.config['MYSQL_DATABASE_USER'] = get_config('DATABASE_USER')
app.config['MYSQL_DATABASE_PASSWORD'] = get_config('DATABASE_PW')
app.config['MYSQL_DATABASE_DB'] = get_config('DATABASE_NAME')
app.config['MYSQL_DATABASE_HOST'] = 'localhost'
mysql.init_app(app)

sys.path.insert(0, os.path.dirname(__file__))

def get_initials():
	names = request.headers["Displayname"].split(" ")
	return (names[0][0] + names[-1][0])

def get_netid():
	return request.headers["Eppn"].split("@")[0]

def get_uid():
	return int(secrets.randbits(28)) 

def sql_fetch(all_records, query, *args):
	conn = mysql.connect()
	data = None
	with conn:
		with conn.cursor() as cursor:
			cursor.execute("select " + query, args)
			if all_records:
				data = cursor.fetchall()
			else:
				data = cursor.fetchone()
	return data

def sql_insert(query, *args):
	conn = mysql.connect()
	with conn:
		with conn.cursor() as cursor:
			cursor.execute("insert into " + query, args)
		conn.commit()

def sql_update(query, *args):
	conn = mysql.connect()
	with conn:
		with conn.cursor() as cursor:
			cursor.execute("update " + query, args)
		conn.commit()

def sql_delete(query, *args):
	conn = mysql.connect()
	with conn:
		with conn.cursor() as cursor:
			cursor.execute("delete from " + query, args)
		conn.commit()

def is_user_unrestricted():
	return (request.headers["Eppn"] in unrestricted_emails)

def is_project_restricted(uid):
	project_name = sql_fetch(False, "Project from Samples where UID=%s", int(uid))[0]
	return (project_name in restricted_projects)

def restricted_check(uid):
	if is_project_restricted(uid):
		return is_user_unrestricted()
	return True

def unrestricted_users():
	emails = []
	for email in all_emails:
		for unrestricted in unrestricted_emails:
			if unrestricted in email:
				emails.append(email)
				break
	return emails

def get_sample_name(uid):
	sample_data = sql_fetch(False, "* from Samples where UID=%s", int(uid))
	sample_name = ''
	if sample_data[1] != '':
		sample_name += sample_data[1]
		if sample_data[2] != '':
			sample_name += '-' + sample_data[2]
			if sample_data[3] != '':
				sample_name += '-' + sample_data[3]
	return sample_name

def get_sample_name_cached(sample_data):
	sample_name = ''
	if sample_data[1] != '':
		sample_name += sample_data[1]
		if sample_data[2] != '':
			sample_name += '-' + sample_data[2]
			if sample_data[3] != '':
				sample_name += '-' + sample_data[3]
	return sample_name

def sanitize(x, full=True):
	if full:
		return re.sub(r'[\'\"\&\$\<\>]', '', x)
	return re.sub(r'[\'\"\&\$]', '', x)

def simple_escape(input_string):
	return input_string.replace('\\', '\\\\').replace('\'', '\\\'').replace('%', ' percent')

def send_email(addresses, subject, message):
	if len(addresses) > 0:
		addresses_escaped = simple_escape(addresses.encode('ascii', 'ignore').decode())
		subject_escaped = simple_escape(subject.encode('ascii', 'ignore').decode())
		message_escaped = simple_escape(message.encode('ascii', 'ignore').decode())
		os.system("echo -e \'" + addresses_escaped + "\\n" + subject_escaped + "\\n" + message_escaped + "\' | ~/sample_tracking/send_email.py &")

def parse_time(x):
	if type(x) is datetime:
		return x.strftime('%Y-%m-%d')
	return x

def get_process_steps(uid):
	return sql_fetch(True, "* from Steps where (UID=%s)", uid)

def has_process_step(uid, process_name):
	return (len(sql_fetch(True, "* from Steps where (UID=%s and ProcessName=%s)", uid, process_name)) > 0)

def get_process_step_cached(cache, process_name):
	for entry in cache:
		if entry[2] == process_name:
			return entry
	return None

def has_process_step_cached(cache, process_name):
	for entry in cache:
		if entry[2] == process_name:
			return True
	return False

def get_process_variable(uid, process_name, process_variable_name):
	process_steps = sql_fetch(True, "* from Steps where (UID=%s and ProcessName=%s)", uid, process_name)
	for row in process_steps:
		if len(row) >= 4:
			process_info = row[3]
			if process_info is not None:
				for process_info_segment in process_info.split(","):
					if "=" in process_info_segment:
						if process_variable_name == process_info_segment.split("=")[0]:
							return process_info_segment.split("=")[1]
	return None

def get_process_variable_cached(cache, process_variable_name):
	if cache == None or len(cache) < 3:
		return None
	process_info = cache[3]
	if process_info is not None:
		for process_info_segment in process_info.split(","):
			if "=" in process_info_segment:
				if process_variable_name == process_info_segment.split("=")[0]:
					return process_info_segment.split("=")[1]
	return None

def get_last_update(uid):
	step_dates = [x[0] for x in sql_fetch(True, "Completed from Steps where UID=%s", uid) if x[0] is not None]
	step_dates.append(sql_fetch(False, "Creation from Samples where UID=%s", uid)[0])
	step_dates.sort()
	
	return parse_time(step_dates[-1])

def get_last_update_cached(cache_sample, cache_steps):
	step_dates = [x[6] for x in cache_steps if x[6] is not None]
	step_dates.append(cache_sample[6])
	step_dates.sort()
	
	return parse_time(step_dates[-1])

def to_yesno(x):
	return ("Yes" if x else "No")

@app.route("/")
def show_all():
	data = None
	if is_user_unrestricted() or len(restricted_projects)==0:
		data = sql_fetch(True, "* from Samples")
	else:
		data = sql_fetch(True, "* from Samples where "+' AND '.join([f"Project != '{x}'" for x in restricted_projects]))
	
	new_data = []
	for row in data:
		new_row = []
		for j in range(0, len(row)):
			new_row.append(row[j])
		new_data.append(new_row)
	
	projects = list(set([x[0] for x in sql_fetch(True, "Project from Samples")]))
	locations = list(set([x[0] for x in sql_fetch(True, "Location from Samples")]))
	if '' in projects:
		projects.remove('')
	if '' in locations:
		locations.remove('')
	
	return render_template("index.html", all_rows=new_data, all_projects=projects, all_locations=locations)

@app.route("/process")
def show_processes():
	return render_template("templates.html")

@app.route("/process_variables")
def show_process_variables():
	return render_template("template_variables.html", process_name=sanitize(request.args.get('name')), process_name_escaped=quote(sanitize(request.args.get('name'))))

@app.route("/todo")
def show_todo():
	return render_template("todo.html")

@app.route("/todo.json")
def todo_json():
	user_name = request.headers["Displayname"]
	user_email = request.headers["Eppn"]
	user_current = f"{user_name} <{user_email}>"
	user_planned = f"{user_name} <{user_email}> (planned)"
	data = sql_fetch(True, "UID, ID from Steps where ((User=%s or User=%s) and Completed is null)", user_current, user_planned)
	todo = []
	for row in data:
		uid = row[0]
		step_id = row[1]
		if restricted_check(uid):
			prior_steps = sql_fetch(True, "ID from Steps where (UID=%s and ID<%s and Completed is null)", uid, step_id)
			line_data = [x for x in sql_fetch(False, "* from Steps where (UID=%s and ID=%s)", uid, step_id)]
			line_data.append("Yes" if len(prior_steps) == 0 else "No")
			todo.append(line_data)
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Steps'")
	json_out = []
	for j in range(0, len(todo)):
		json_col = {}
		json_col['SampleName'] = get_sample_name(todo[j][0])
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(todo[j][i])
		json_col["Current"] = parse_time(todo[j][-1])
		json_out.append(json_col)
	return json_out

@app.route("/instruments")
def show_instruments():
	return render_template("instruments.html")

@app.route("/instruments.json")
def todo_instruments():
	data = sql_fetch(True, "UID, ID from Steps where Active=1")
	instruments = []
	for row in data:
		if restricted_check(row[0]):
			instruments.append(sql_fetch(False, "* from Steps where (UID=%s and ID=%s)", row[0], row[1]))
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Steps'")
	json_out = []
	for j in range(0, len(instruments)):
		json_col = {}
		json_col['SampleName'] = get_sample_name(instruments[j][0])
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(instruments[j][i])
		json_out.append(json_col)
	return json_out

@app.route("/samples.json")
def samples_json():
	data = None
	if is_user_unrestricted() or len(restricted_projects)==0:
		if "uid" in request.args:
			data = sql_fetch(True, "* from Samples where UID=%s", sanitize(request.args.get("uid")))
		else:
			data = sql_fetch(True, "* from Samples")
	else:
		if "uid" in request.args:
			data = sql_fetch(True, "* from Samples where ("+(' AND '.join([f"Project != '{x}'" for x in restricted_projects]))+" AND UID=%s)", sanitize(request.args.get("uid")))
		else:
			data = sql_fetch(True, "* from Samples where "+' AND '.join([f"Project != '{x}'" for x in restricted_projects]))
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Samples'")
	json_out = []
	for j in range(0, len(data)):
		json_col = {}
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(data[j][i])
		
		uid = json_col["UID"]
		# Extra columns
		cache = get_process_steps(uid)
		#oxidation_data = get_process_step_cached(cache, "Oxidation")
		#json_col["OxidationPlanned"] = to_yesno(oxidation_data != None)
		#json_col["Oxidized"] = to_yesno(oxidation_data != None and oxidation_data[6] != None)
		#print_data = get_process_step_cached(cache, "Nanoscribe Fabrication")
		#json_col["PrintPlanned"] = to_yesno(print_data != None)
		#json_col["Printed"] = to_yesno(print_data != None and print_data[6] != None)
		#etch_data = get_process_step_cached(cache, "Electrochemical Etch")
		#json_col["Thickness"] = get_process_variable_cached(etch_data, "Thickness")
		#json_col["Quality"] = get_process_variable_cached(etch_data, "Quality")
		#json_col["CurrentDensity"] = get_process_variable_cached(etch_data, "J")
		#json_col["FilmDiameter"] = get_process_variable_cached(etch_data, "Chamber")
		#json_col["Transferred"] = get_process_variable_cached(etch_data, "Transferred")
		#json_col["PorousFilm"] = to_yesno(etch_data != None)
		json_col["LastUpdate"] = get_last_update_cached(data[j], cache)
		json_col["SampleName"] = get_sample_name_cached(data[j])
		
		json_out.append(json_col)
	
	return json_out

@app.route("/add", methods=['POST'])
def add_sample():
	new_uid = get_uid()
	project = sanitize(request.form['project_secondary'] if 'project_secondary' in request.form else request.form['project'])
	location = sanitize(request.form['location_secondary'] if 'location_secondary' in request.form else request.form['location'])
	existing_ids = sql_fetch(True, "* from Samples where (Project=%s AND ID=%s AND Location=%s)", project,sanitize(request.form['id']),location)
	if len(existing_ids) > 0:
		return render_template("redirect_index.html")
	sql_insert("Samples (UID, Project, ID, Location, Description) values (%s, %s, %s, %s, %s)",
		new_uid,project,sanitize(request.form['id']),location,sanitize(request.form['description']))
	
	if 'template' in request.form:
		name_components = request.form['template'].split('-')
		project_template = ''
		if len(name_components) > 0:
			project_template = sanitize(name_components[0])
		sampleid_template = ''
		if len(name_components) > 1:
			sampleid_template = sanitize(name_components[1])
		location_template = ''
		if len(name_components) > 2:
			location_template = sanitize(name_components[2])
		
		template_ids = sql_fetch(True, "UID from Samples where (Project=%s AND ID=%s AND Location=%s)", project_template,sampleid_template,location_template)
		if len(template_ids) == 1:
			template_id = template_ids[0][0]
			template_steps = sql_fetch(True, "* from Steps where (UID=%s)", template_id)
			for row in template_steps:
				editable_row = list(row)
				editable_row[0] = new_uid
				sql_insert("Steps values(" + ', '.join(["%s"] * len(editable_row)) + ")", *editable_row)
	
	return render_template("redirect_show.html", uid=new_uid)

@app.route("/add_step", methods=['POST'])
def add_step():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	data = sql_fetch(True, "* from Steps where UID=%s", int(sanitize(request.form['uid'])))
	step_id = 1
	for row in data:
		if row[1] + 1 > step_id:
			step_id = row[1] + 1
	
	process_name = sanitize(request.form['processname'])
	if process_name == 'other':
		process_name = sanitize(request.form['processname_secondary'])
	sql_insert("Steps (UID, ID, ProcessName) values (%s, %s, %s)",
		int(sanitize(request.form['uid'])), step_id, process_name)
	
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/get_next_sample")
def get_next_sample():
	project = sanitize(request.args.get('project'))
	
	sample_id = 1
	if project in restricted_projects and not is_user_unrestricted():
		return Response(str(sample_id), mimetype='text/plain')
	
	data = sql_fetch(True, "ID from Samples where Project=%s", project)
	for row in data:
		id_search = re.search('[0-9]+', row[0])
		if id_search:
			read_id = int(id_search.group(0))
			if read_id + 1 > sample_id:
				sample_id = read_id + 1
	
	return Response(str(sample_id), mimetype='text/plain')

@app.route("/add_process", methods=['POST'])
def add_process():
	data = sql_fetch(True, "* from Processes")
	step_id = 1
	for row in data:
		if row[1] + 1 > step_id:
			step_id = row[1] + 1
	
	sql_insert("Processes (ProcessName, ID) values (%s, %s)", sanitize(request.form['processname']), step_id)
	
	return render_template("redirect_templates.html")

@app.route("/add_process_variable", methods=['POST'])
def add_process_variable():
	data = sql_fetch(True, "* from ProcessOptions where ProcessName=%s", sanitize(request.form['processname']))
	step_id = 1
	for row in data:
		if row[1] + 1 > step_id:
			step_id = row[1] + 1
	
	sql_insert("ProcessOptions (ProcessName, ID, Variable) values (%s, %s, %s)", sanitize(request.form['processname']), step_id, sanitize(request.form['variable']))
	
	return render_template("redirect_template_variables.html", process_name=sanitize(request.form['processname']))

@app.route("/delete_process", methods=['POST'])
def delete_process():
	process_removed = int(sanitize(request.form['id']))
	sql_delete("Processes where ID=%s", process_removed)
	all_processes = sql_fetch(True, "* from Processes")
	
	processes_to_correct = []
	for step in all_processes:
		if step[1] > process_removed:
			processes_to_correct.append(step[1])
	processes_to_correct.sort()
	for process in processes_to_correct:
		sql_update("Processes set ID=%s where ID=%s", process - 1, process)
	return render_template("redirect_templates.html")

@app.route("/delete_process_variable", methods=['POST'])
def delete_process_variable():
	process_removed = int(sanitize(request.form['id']))
	sql_delete("ProcessOptions where (ID=%s and ProcessName=%s)", process_removed, sanitize(request.form['processname']))
	all_processes = sql_fetch(True, "* from ProcessOptions where ProcessName=%s", sanitize(request.form['processname']))
	
	processes_to_correct = []
	for step in all_processes:
		if step[1] > process_removed:
			processes_to_correct.append(step[1])
	processes_to_correct.sort()
	for process in processes_to_correct:
		sql_update("ProcessOptions set ID=%s where (ID=%s and ProcessName=%s)", process - 1, process, sanitize(request.form['processname']))
	return render_template("redirect_template_variables.html", process_name=sanitize(request.form['processname']))

@app.route("/edit_process", methods=['POST'])
def edit_process():
	headers = [x[0] for x in sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Processes'")]
	desired_field = sanitize(request.form['field'])
	if desired_field in headers:
		if desired_field == "ID":
			old_id = int(sanitize(request.form['id']))
			new_id = int(sanitize(request.form['new_value']))
			if new_id > 0:
				sql_update(f"Processes set ID=0 where ID=%s",
					old_id)
				
				all_processes = sql_fetch(True, "* from Processes")
				steps_to_process = []
				for process in all_processes:
					if process[1] > old_id:
						steps_to_process.append(process[1])
				steps_to_process.sort()
				for process in steps_to_process:
					sql_update("Processes set ID=%s where ID=%s", process - 1, process)
				
				all_step_ids = [x[0] for x in sql_fetch(True, "ID from Processes")]
				all_step_ids.sort()
				if new_id > all_step_ids[-1]:
					new_id = all_step_ids[-1] + 1
				
				steps_to_process = []
				for step_id in all_step_ids:
					if step_id == new_id or (step_id - 1) in steps_to_process:
						steps_to_process.append(step_id)
				steps_to_process.sort()
				steps_to_process = steps_to_process[::-1]
				for process in steps_to_process:
					sql_update("Processes set ID=%s where ID=%s", process + 1, process)
				
				sql_update(f"Processes set ID=%s where ID=0", new_id)
		else:
			if sanitize(request.form['new_value']) == "":
				sql_update(f"Processes set {desired_field}=null where ID=%s", sanitize(request.form['id']))
			else:
				sql_update(f"Processes set {desired_field}=%s where ID=%s", sanitize(request.form['new_value']),sanitize(request.form['id']))
	return render_template("redirect_templates.html")


@app.route("/edit_process_variable", methods=['POST'])
def edit_process_variable():
	process_name = sanitize(request.form['processname'])
	headers = [x[0] for x in sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'ProcessOptions'")]
	desired_field = sanitize(request.form['field'])
	if desired_field in headers:
		if desired_field == "ID":
			old_id = int(sanitize(request.form['id']))
			new_id = int(sanitize(request.form['new_value']))
			if new_id > 0:
				sql_update(f"ProcessOptions set ID=0 where (ID=%s and ProcessName=%s)",
					old_id, process_name)
				
				all_processes = sql_fetch(True, "* from ProcessOptions where ProcessName=%s", process_name)
				steps_to_process = []
				for process in all_processes:
					if process[1] > old_id:
						steps_to_process.append(process[1])
				steps_to_process.sort()
				for process in steps_to_process:
					sql_update("ProcessOptions set ID=%s where (ID=%s and ProcessName=%s)", process - 1, process, process_name)
				
				all_step_ids = [x[0] for x in sql_fetch(True, "ID from ProcessOptions where ProcessName=%s", process_name)]
				all_step_ids.sort()
				if new_id > all_step_ids[-1]:
					new_id = all_step_ids[-1] + 1
				
				steps_to_process = []
				for step_id in all_step_ids:
					if step_id == new_id or (step_id - 1) in steps_to_process:
						steps_to_process.append(step_id)
				steps_to_process.sort()
				steps_to_process = steps_to_process[::-1]
				for process in steps_to_process:
					sql_update("ProcessOptions set ID=%s where (ID=%s and ProcessName=%s)", process + 1, process, process_name)
				
				sql_update(f"ProcessOptions set ID=%s where (ID=0 and ProcessName=%s)", new_id, process_name)
		else:
			if sanitize(request.form['new_value']) == "":
				sql_update(f"ProcessOptions set {desired_field}=null where (ID=%s and ProcessName=%s)", sanitize(request.form['id']), process_name)
			else:
				sql_update(f"ProcessOptions set {desired_field}=%s where (ID=%s and ProcessName=%s)", sanitize(request.form['new_value']),sanitize(request.form['id']), process_name)
	return render_template("redirect_template_variables.html", process_name=process_name)

@app.route("/process_variable_table.json")
def get_process_variable_table():
	step = sql_fetch(False, '* from Steps where (UID=%s and ID=%s)', sanitize(request.args.get('uid')), sanitize(request.args.get('id')))
	process_name = step[2]
	process_info = step[3]
	
	process_variables_entered_dict = {}
	if process_info is not None:
		process_variables_entered = [x.split('=') for x in process_info.split(',')]
		for process_variable_data in process_variables_entered:
			if len(process_variable_data) > 1:
				process_variables_entered_dict[process_variable_data[0]] = process_variable_data[1]
			else:
				process_variables_entered_dict[process_variable_data[0]] = ''
	
	process_variables_template = sql_fetch(True, '* from ProcessOptions where ProcessName=%s', process_name)
	
	process_variables_all = []
	
	next_idx = 0
	for process_variable_data in process_variables_template:
		json_col = {}
		json_col['Custom'] = False
		json_col['Variable'] = process_variable_data[2]
		json_col['Options'] = process_variable_data[3]
		json_col['Unit'] = process_variable_data[4]
		if process_variable_data[2] in process_variables_entered_dict.keys():
			variable_value = process_variables_entered_dict[process_variable_data[2]]
			json_col['VariableValue'] = variable_value
			
			del process_variables_entered_dict[process_variable_data[2]]
		else:
			json_col['VariableValue'] = None
		json_col['Index'] = next_idx
		next_idx += 1
		process_variables_all.append(json_col)
	
	for process_variable in process_variables_entered_dict.keys():
		json_col = {}
		json_col['Custom'] = True
		json_col['Variable'] = process_variable
		json_col['Options'] = ''
		json_col['Unit'] = ''
		variable_value = process_variables_entered_dict[process_variable]
		json_col['VariableValue'] = variable_value
		json_col['Index'] = next_idx
		next_idx += 1
		process_variables_all.append(json_col)
	
	return process_variables_all

@app.route("/edit_params", methods=['POST'])
def edit_params():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	uid = int(sanitize(request.form['uid']))
	step_id = int(sanitize(request.form['id']))
	
	process_info = []
	
	idx = 0
	while ('variable_' + str(idx)) in request.form:
		variable_name = sanitize(request.form['variable_' + str(idx)])
		variable_value = ''
		if ('variablevalue_secondary_' + str(idx)) in request.form and request.form['variablevalue_secondary_' + str(idx)] != '':
			variable_value = sanitize(request.form['variablevalue_secondary_' + str(idx)])
		else:
			variable_value = sanitize(request.form['variablevalue_' + str(idx)])
			
		variable_custom = sanitize(request.form['custom_' + str(idx)])
		if variable_custom == 'true':
			if variable_name != '':
				if variable_value != '':
					process_info.append(variable_name + '=' + variable_value)
				else:
					process_info.append(variable_name)
		else:
			if variable_value != '':
				process_info.append(variable_name + '=' + variable_value)
		
		idx += 1
	
	process_info = ','.join(process_info)
	
	if process_info == '':
		sql_update(f"Steps set ProcessInfo=null where (UID=%s AND ID=%s)",
					sanitize(request.form['uid']),sanitize(request.form['id']))
	else:
		sql_update(f"Steps set ProcessInfo=%s where (UID=%s AND ID=%s)",
					process_info,sanitize(request.form['uid']),sanitize(request.form['id']))
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/delete_step", methods=['POST'])
def delete_step():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	step_removed = int(sanitize(request.form['id']))
	sql_delete("Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), step_removed)
	all_steps = sql_fetch(True, "* from Steps where UID=%s", int(sanitize(request.form['uid'])))
	
	steps_to_correct = []
	for step in all_steps:
		if step[1] > step_removed:
			steps_to_correct.append(step[1])
	steps_to_correct.sort()
	for step in steps_to_correct:
		sql_update("Steps set ID=%s where (UID=%s and ID=%s)", step - 1, int(sanitize(request.form['uid'])), step)
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/edit_step", methods=['POST'])
def edit_step():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	headers = [x[0] for x in sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Steps'")]
	desired_field = sanitize(request.form['field'])
	if desired_field in headers and desired_field != "UID":
		if desired_field == "ID":
			old_id = int(sanitize(request.form['id']))
			new_id = int(sanitize(request.form['new_value']))
			if new_id > 0:
				sql_update(f"Steps set ID=0 where (UID=%s AND ID=%s)",
					sanitize(request.form['uid']),old_id)
				
				all_steps = sql_fetch(True, "* from Steps where UID=%s", int(sanitize(request.form['uid'])))
				steps_to_correct = []
				for step in all_steps:
					if step[1] > old_id:
						steps_to_correct.append(step[1])
				steps_to_correct.sort()
				for step in steps_to_correct:
					sql_update("Steps set ID=%s where (UID=%s and ID=%s)", step - 1, int(sanitize(request.form['uid'])), step)
				
				all_step_ids = [x[0] for x in sql_fetch(True, "ID from Steps where UID=%s", int(sanitize(request.form['uid'])))]
				all_step_ids.sort()
				if new_id > all_step_ids[-1]:
					new_id = all_step_ids[-1] + 1
				
				steps_to_correct = []
				for step_id in all_step_ids:
					if step_id == new_id or (step_id - 1) in steps_to_correct:
						steps_to_correct.append(step_id)
				steps_to_correct.sort()
				steps_to_correct = steps_to_correct[::-1]
				for step in steps_to_correct:
					sql_update("Steps set ID=%s where (UID=%s and ID=%s)", step + 1, int(sanitize(request.form['uid'])), step)
				
				sql_update(f"Steps set ID=%s where (UID=%s AND ID=0)",
					new_id,sanitize(request.form['uid']))
		elif desired_field == "User":
			if sanitize(request.form['new_value']) == "":
				sql_update(f"Steps set {desired_field}=null where (UID=%s AND ID=%s)",
				sanitize(request.form['uid']),sanitize(request.form['id']))
			else:
				sql_update(f"Steps set {desired_field}=%s where (UID=%s AND ID=%s)",
				sanitize(request.form['new_value'], False) + (" (planned)" if ('planned' in request.form) else ''),sanitize(request.form['uid']),sanitize(request.form['id']))
		elif desired_field == "Active":
			user_name = request.headers["Displayname"]
			user_email = request.headers["Eppn"]
			current_user = f"{user_name} <{user_email}>"
			prev_user = sql_fetch(False, "User from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
			if request.form['new_value'] == "1":
				sql_update(f"Steps set Active=1, User=%s where (UID=%s AND ID=%s)",
					current_user,sanitize(request.form['uid']),sanitize(request.form['id']))
			else:
				sql_update(f"Steps set Active=0, User=%s where (UID=%s AND ID=%s)",
					prev_user.removesuffix(' (planned)') + ' (planned)',sanitize(request.form['uid']),sanitize(request.form['id']))
		else:
			if sanitize(request.form['new_value']) == "":
				sql_update(f"Steps set {desired_field}=null where (UID=%s AND ID=%s)",
				sanitize(request.form['uid']),sanitize(request.form['id']))
			else:
				sql_update(f"Steps set {desired_field}=%s where (UID=%s AND ID=%s)",
				sanitize(request.form['new_value']),sanitize(request.form['uid']),sanitize(request.form['id']))
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/complete_step", methods=['POST'])
def complete_step():
	user_name = request.headers["Displayname"]
	user_email = request.headers["Eppn"]
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	last_complete_time = sql_fetch(False, "Completed from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
	if last_complete_time != None:
		return render_template("redirect_show.html", uid=sanitize(request.form['uid']))
	
	email_data = all_emails
	if is_project_restricted(sanitize(request.form['uid'])):
		email_data = unrestricted_users()
	
	watcher_email = ''
	for email in email_data:
		if user_email != '' and user_email in email:
			watcher_email = email
			break
	
	if len(sql_fetch(True, "* from Watch where (UID=%s AND Email=%s)", int(sanitize(request.form['uid'])), watcher_email)) == 0:
		sql_insert("Watch (UID, Email) values (%s, %s)", int(sanitize(request.form['uid'])), watcher_email)
	
	sql_update("Steps set Completed=now() where (UID=%s AND ID=%s)", #, Active=0
			int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))
	
	if sql_fetch(False, "User from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id']))) == None or sql_fetch(False, "User from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0] == None or sql_fetch(False, "User from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0].endswith(' (planned)'):
		sql_update("Steps set User=%s where (UID=%s AND ID=%s)",
			f"{user_name} <{user_email}>", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))
	
	all_watchers = ",".join([x[0] for x in sql_fetch(True, "Email from Watch where UID=%s", int(sanitize(request.form['uid']))) if x[0] in all_emails])
	sample_name = get_sample_name(sanitize(request.form['uid']))
	step_name = sql_fetch(False, "ProcessName from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
	current_day = datetime.now().strftime('%Y-%m-%d')
	process_info = sql_fetch(False, "ProcessInfo from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
	
	process_info = format_process_info(step_name, process_info, False)
	
	comment = sql_fetch(False, "Comments from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
	files = sql_fetch(False, "FileLink from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
	if files is None:
		files = ""
	else:
		files = " \nFiles: " + files
	
	completed_user = sql_fetch(False, "User from Steps where (UID=%s AND ID=%s)", int(sanitize(request.form['uid'])), int(sanitize(request.form['id'])))[0]
	completed_user = completed_user.replace("<", "(").replace(">", ")")
	
	full_process_flow = []
	process_flow_data = sql_fetch(True, "ID, ProcessName, ProcessInfo, Comments, FileLink, Completed, User from Steps where (UID=%s)", int(sanitize(request.form['uid']))) #, Active
	
	process_flow_data = list(process_flow_data)
	process_flow_data.sort(key=lambda x: x[0])
	for process_data in process_flow_data:
		current_indicator = ''
		if process_data[0] == int(sanitize(request.form['id'])):
			current_indicator = '--> '
		full_process_flow.append(f"{current_indicator}Step {process_data[0]}: {process_data[1]}. {format_process_info(process_data[1], process_data[2], False)}")
	
	full_process_flow = '\n'.join(full_process_flow)
	
	send_email(all_watchers, f"Sample update: {sample_name}", f"{step_name} completed by {completed_user} on {current_day}. \nComment: {comment} \n\nProcess details: {process_info}{files} \n\nIf you would like to see more details or unwatch this sample, go to https://{domain}/samples/show?uid={request.form['uid']} .\n\nFull process flow: \n{full_process_flow}")
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/show")
def show_sample():
	if not restricted_check(sanitize(request.args.get('uid'))):
		return render_template("redirect_index.html")
	data = sql_fetch(True, "* from Steps where UID=%s", sanitize(request.args.get('uid')))
	data2 = sql_fetch(False, "* from Samples where UID=%s", sanitize(request.args.get('uid')))
	data3 = sql_fetch(True, "* from Watch where UID=%s", sanitize(request.args.get('uid')))
	email_data = all_emails
	if is_project_restricted(sanitize(request.args.get('uid'))):
		email_data = unrestricted_users()
	
	process_names = [x[0] for x in sql_fetch(True, "ProcessName from Processes order by ID asc")]
	
	return render_template("show.html", uid=sanitize(request.args.get('uid')), sample_name=get_sample_name(sanitize(request.args.get('uid'))), all_emails=email_data, process_names=process_names)

@app.route("/watchers.json")
def get_watchers():
	if not restricted_check(sanitize(request.args.get('uid'))):
		return []
	data = sql_fetch(True, "* from Watch where UID=%s", sanitize(request.args.get('uid')))
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Watch'")
	json_out = []
	for j in range(0, len(data)):
		json_col = {}
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(data[j][i])
		json_out.append(json_col)
	
	return json_out

def get_options_from_rows(rows, process_variable_name):
	for row in rows:
		if process_variable_name == row[2]:
			return row
	return ()

def format_process_info(process_step, process_info, include_html=True):
	process_info_row = []
	process_template = sql_fetch(True, "* from ProcessOptions where ProcessName=%s", process_step)
	
	if process_info is not None:
		for process_variable in process_info.split(','):
			process_info_formatted = ''
			
			if '=' in process_variable:
				process_variable_name = process_variable.split('=')[0]
				process_variable_value = process_variable.split('=')[1]
				
				process_options = get_options_from_rows(process_template, process_variable_name)
				
				process_variable_tooltip = ''
				if len(process_options) >= 4 and process_options[3] is not None:
					for process_option in process_options[3].split(','):
						if '|' in process_option:
							if process_variable_value == process_option.split('|')[0]:
								process_variable_tooltip = process_option.split('|')[1]
								break
				
				if process_variable_tooltip == '' or not include_html:
					process_info_formatted += process_variable_name + " = " + process_variable_value
				else:
					process_info_formatted += process_variable_name + ' = <span class="customtooltip" title="' + process_variable_tooltip + '">' + process_variable_value + '</span>'
			
				if len(process_options) >= 5 and process_options[4] is not None:
					process_info_formatted += " " + process_options[4]
			else:
				process_info_formatted += process_variable
			
			process_info_row.append(process_info_formatted)
	
	return ', '.join(process_info_row)

@app.route("/steps.json")
def get_steps():
	if not restricted_check(sanitize(request.args.get('uid'))):
		return []
	data = sql_fetch(True, "* from Steps where UID=%s", sanitize(request.args.get('uid')))
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Steps'")
	json_out = []
	for j in range(0, len(data)):
		json_col = {}
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(data[j][i])
		json_out.append(json_col)
	
	json_out = sorted(json_out, key=lambda x: x["ID"])
	
	for row in json_out:
		process_step = row['ProcessName']
		process_info = row['ProcessInfo']
		row['ProcessInfoFormatted'] = format_process_info(process_step, process_info)
	
	return json_out

@app.route("/steps.csv")
def get_steps_csv():
	if not restricted_check(sanitize(request.args.get('uid'))):
		return []
	data = list(sql_fetch(True, "* from Steps where UID=%s", sanitize(request.args.get('uid'))))
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Steps'")
	
	csv_out = io.StringIO()
	writer = csv.writer(csv_out)
	writer.writerow([x[0] for x in headers][2:])
	data.sort(key=lambda x: x[1])
	for row in data:
		writer.writerow(row[2:])
	
	return csv_out.getvalue()

@app.route("/change_steps.csv", methods=['POST'])
def set_steps_csv():
	if not restricted_check(sanitize(request.form['uid'])):
		return []
	
	if request.form['replace'] == 'true':
		sql_delete("Steps where UID=%s", int(sanitize(request.form['uid'])))
	
	data = list(sql_fetch(True, "* from Steps where UID=%s", sanitize(request.form['uid'])))
	
	step_id = 1
	for row in data:
		if row[1] + 1 > step_id:
			step_id = row[1] + 1
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Steps'")
	
	file = io.StringIO(request.files['steps'].read().decode())
	
	reader = csv.reader(file, delimiter=",", quotechar="\"")
	
	column_names = ["ProcessName", "ProcessInfo", "Comments", "FileLink", "Completed", "User"]
	for raw_row in reader:
		row = [None if x == '' else x for x in raw_row]
		if len(row) > 0 and len(row) <= len(column_names) and row[0] != 'ProcessName':
			sql_insert("Steps (UID, ID, "+", ".join(column_names[0:len(row)])+") values (%s, %s, "+", ".join(["%s"]*len(row))+")", sanitize(request.form['uid']), step_id, *row)
			step_id += 1
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/process.json")
def get_processes():
	data = sql_fetch(True, "* from Processes")
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Processes'")
	json_out = []
	for j in range(0, len(data)):
		json_col = {}
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(data[j][i])
		json_out.append(json_col)
	
	json_out = sorted(json_out, key=lambda x: x["ID"])
	
	return json_out

@app.route("/process_variables.json")
def get_process_variables():
	data = sql_fetch(True, "* from ProcessOptions where ProcessName=%s", sanitize(request.args.get("name")))
	
	headers = sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'ProcessOptions'")
	json_out = []
	for j in range(0, len(data)):
		json_col = {}
		for i in range(0, len(headers)):
			json_col[headers[i][0]] = parse_time(data[j][i])
		json_out.append(json_col)
	
	json_out = sorted(json_out, key=lambda x: x["ID"])
	
	return json_out

@app.route("/delete", methods=['POST'])
def delete_sample():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	sql_delete("Samples where UID=%s", int(sanitize(request.form['uid'])))
	sql_delete("Steps where UID=%s", int(sanitize(request.form['uid'])))
	sql_delete("Watch where UID=%s", int(sanitize(request.form['uid'])))
	return render_template("redirect_index.html")

@app.route("/edit", methods=['POST'])
def edit_sample():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	headers = [x[0] for x in sql_fetch(True, "COLUMN_NAME from INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = N'Samples'")]
	desired_field = sanitize(request.form['field'])
	if desired_field in headers and desired_field != "UID":
		if sanitize(request.form['new_value']) == "" and desired_field != "Project" and desired_field != "ID" and desired_field != "Location":
			sql_update(f"Samples set {desired_field}=null where UID=%s",
				sanitize(request.form['uid']))
		else:
			sql_update(f"Samples set {desired_field}=%s where UID=%s",
				sanitize(request.form['new_value']),sanitize(request.form['uid']))
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/add_watcher", methods=['POST'])
def add_watcher():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	
	email_data = all_emails
	if is_project_restricted(sanitize(request.form['uid'])):
		email_data = unrestricted_users()
	
	if len(sql_fetch(True, "* from Watch where (UID=%s AND Email=%s)", int(sanitize(request.form['uid'])), email_data[int(sanitize(request.form['email'], False))])) > 0:
		return render_template("redirect_show.html", uid=sanitize(request.form['uid']))
	sql_insert("Watch (UID, Email) values (%s, %s)", int(sanitize(request.form['uid'])), email_data[int(sanitize(request.form['email'], False))])
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

@app.route("/delete_watcher", methods=['POST'])
def delete_watcher():
	if not restricted_check(sanitize(request.form['uid'])):
		return render_template("redirect_index.html")
	sql_delete("Watch where (UID=%s AND Email=%s)", int(sanitize(request.form['uid'])), sanitize(request.form['email'], False))
	return render_template("redirect_show.html", uid=sanitize(request.form['uid']))

if __name__ == '__main__':
	app.run()
