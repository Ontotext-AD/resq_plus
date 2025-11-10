
import psycopg2

input_password = input()

conn = psycopg2.connect(dbname="shm2" , user="ontotext1" , password=input_password , host="integration-tests.postgres.database.azure.com" , port="5432")

# Open a cursor to perform database operations
cur = conn.cursor()

# Query the database and obtain data as Python objects
cur.execute("select cholesterol from strokehealthcaremodel_inpatientcase si where case_id = 1")
# cur.execute("select diastolic_pressure from strokehealthcaremodel_inpatientcase si where case_id = 1")
# cur.execute("select systolic_pressure from strokehealthcaremodel_inpatientcase si where case_id = 1")
print(cur.fetchone())
# (1, 100, "abc'def")

# Make the changes to the database persistent
# >>> conn.commit()

# Close communication with the database
cur.close()
conn.close()

# dbname – the database name (database is a deprecated alias)

# user – user name used to authenticate

# password – password used to authenticate

# host – database host address (defaults to UNIX socket if not provided)

# port – connection port number (defaults to 5432 if not provided)