#!/usr/bin/python3

import smtplib
import sys
import re
import subprocess
import os

def get_config(name):
	with open(os.path.expanduser("~/sample_tracking/CONFIG")) as f:
		for line in f.readlines():
			if line.startswith('export '):
				line = line[len('export '):]
			s = line.split('=')
			if len(s) == 2 and s[0] == name:
				return s[1].strip("\"\n")
	return ""

domain = get_config('DOMAIN')

all_text = sys.stdin.read().split("\n")
emails = all_text[0]
subject = all_text[1]
message = "\n".join(all_text[2:])

s = smtplib.SMTP("incoming-relays.illinois.edu", 25)
for email in emails.split(","):
	email_only = email
	if "<" in email_only:
		email_only = email_only.split("<")[1].split(">")[0]
	replies = emails.split(",")
	replies.remove(email)
	replies = ",".join(replies)
	mime_msg = f"From: nobody@illinois.edu\nTo: {emails}\nReply-To: {replies}\nContent-Type: text/plain\nSubject: {subject}\n\n{message}"
	s.sendmail("wwwsece@"+domain, email_only, mime_msg)

s.quit()

