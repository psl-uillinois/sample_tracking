# Sample tracking

Installation instructions:
1. Setup an account on https://web.illinois.edu/ or a comparable cPanel service.
2. Copy or clone this directory into a sample_tracking folder in your home directory.
3. Copy CONFIG_DEFAULT to CONFIG. Begin by filling in the domain (e.g., `export DOMAIN=yourgroup.web.illinois.edu`), a comma-separated list of users (e.g., `export USERS="John Doe <jdoe@illinois.edu>,Jane Doe <jdoe2@illinois.edu>"`), and if desired, a comma-separated list of restricted project titles (e.g., `export RESTRICTED_PROJECTS=DARPA`) and a list of email addresses approved to access them (e.g., `export UNRESTRICTED_USERS=jdoe2@illinois.edu`).
4. Enable the following scripts as executables with `chmod +x scriptname`: `backup`, `send_email.py`, `initialize_db.sh`, `CONFIG`.
5. Create a database in cPanel with a username, database name, and password of your choice. Fill these in after the equals sign in CONFIG.
6. Initialize the database by executing `initialize_db.sh` inside the `sample_tracking` directory.
7. Setup automatic backups with by running `crontab cron.txt` inside the `sample_tracking` directory. For off-site backups, set up an external script that transfers files from `~/backups` onto external storage. Backups can be restored with `mysqldump` (see example in `initialize_db.sh`).
8. In cPanel, setup a new Python app, and map your sample tracking directory (e.g., `/home/yourusername/sample_tracking`) to `/samples`.
8. Start the Python app in cPanel.
9. Go to your new website (`.../samples`) and check that everything works. Debug as necessary.
