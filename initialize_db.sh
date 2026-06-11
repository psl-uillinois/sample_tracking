#!/bin/bash
source ~/sample_tracking/CONFIG

mysqldump -u "$DATABASE_USER" "-p$DATABASE_PASSWORD" "$DATABASE_NAME" < "initial_db.sql"