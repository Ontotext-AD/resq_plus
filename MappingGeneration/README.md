# Generate .csv mapping file

**Requirements:**
- OBDA file

**Steps for generation:**

- In obda_to_csv.py, locate main method:

```
if __name__ == '__main__':
    obda_to_csv('obda_file_path','output_csv_path',)
```

- Change parameters to the corresponding input(OBDA) and desired output (CSV) paths
- Run script


**Update mappings in materialization_service**
- In materialization_service, locate: 

`materialization-service\upload\mappings`

- Remove old mappings.csv file
- Add newly generated mappings.csv file
