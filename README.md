

<img width="417" height="158" alt="RESQ+_Logo_Full_Colors_RGB" src="https://github.com/user-attachments/assets/1d3f662d-931a-4941-8e45-4519f1d9d492" />


# Deploy OBDA file

**Requirements for virtualization:**

- GraphDB
- OBDA file

**Steps for deployment:**
1. Create new virtual repository:
    1. Access GraphDB instance
    2. Select "Settings"
    3. Select "Repositories"
    4. Click "Create new repository"
    5. Choose "Ontop Virtual SPARQL"

2. Deploy OBDA file
    1. Name the repository via "Repository ID"
    2. Fill the "Connection Information"
        1. Choose Database driver - PostgreSQL
        2. Host name
        3. Port 
        4. Database name
        5. Username
        6. Password
    3. (OPTIONAL) Test connection
    4. Attach the file - "OBDA or R2RML file"
    5. Create

3. Perform test SPARQL query:

```sql
SELECT * WHERE {
    ?s ?p ?o .
} LIMIT 100
```


***This project has received funding from the European Union’s Horizon Europe research and innovation programme under grant agreement No:101057603. Views and opinions expressed are however those of the author only and do not necessarily reflect those of the European Union or the European Research Executive Agency. Neither the European Union nor the granting authority can be held responsible for them.***

**Project website:** https://www.resqplus.eu/
