# Import required modules
import csv
import sqlite3
import os

# Create the database file
connection = sqlite3.connect('./raw_data/test.db')

# Creating a cursor object to execute SQL queries
cursor = connection.cursor()

# Table Definition
# rb          = RB Number
# date        = Reported Date
# time        = Reported Time
# description = Statute/Ordinance Description
# location    = Occurred Location
# district    = Occurred District
# lat         = Occurred Block LAT
# lon         = Occurred Block LON
create_table = '''CREATE TABLE incidents(
				id INTEGER PRIMARY KEY AUTOINCREMENT,
                rb TEXT NOT NULL,
				date TEXT NOT NULL,
				time TEXT NOT NULL,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                district TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL);
				'''

# Create the table
cursor.execute(create_table)

# Point to the data directory
directory = os.fsencode("./raw_data/")

# Loop through all raw data files
for file in os.listdir(directory):
    filename = os.fsdecode(file)
    if filename.endswith(".csv"): 
        # Opening the file
        file = open("./raw_data/" + filename)

        # Reading the contents of the file
        contents = csv.reader(file)

        # SQL query to insert data into the
        # table
        insert_records = "INSERT INTO incidents (rb, date, time, description, location, district, lat, lon) VALUES(?, ?, ?, ?, ?, ?, ?, ?)"

        # Importing the contents of the file 
        # into our table
        cursor.executemany(insert_records, contents)
        print("Inserted data from: ", filename)
        continue
    else:
        continue

# Delete extra copies of the header row that were inserted
delete_headers = "DELETE FROM incidents WHERE rb = 'RB Number'"
cursor.execute(delete_headers)

# Test query to see if the data loaded
select_all = "SELECT * FROM incidents"
rows = cursor.execute(select_all).fetchall()

# Output to the console screen
for r in rows:
	print(r)

# Commit the changes
connection.commit()

# Close the database connection
connection.close()
