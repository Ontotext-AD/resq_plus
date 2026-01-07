import json
import sys
import os
import requests 

def get_req_headers(host):
    usr = input()
    psw = input()
    if usr is None or psw is None:
        raise ValueError("GDB user and password must be specified")
    auth = f"{host}/rest/login/{usr}"
    auth_headers = {"X-GraphDB-Password": psw}
    auth = requests.post(auth, headers=auth_headers)
    auth_token = auth.headers.get("Authorization")
    return auth_token


def parse_data(repo, query):
    gdb_url = "https://resqplus.ontotext.com/"
    repo = "ResQvirt"
    if gdb_url is None:
        raise ValueError("[!] GDB_HOST is not set properly, exiting.")
    if "repositories" in gdb_url:
        gdb_url = gdb_url.split("repositories")[0]
    repository = gdb_url + f"/repositories/{repo}"
    atoken = get_req_headers(gdb_url)
    headers = {"Accept":"application/sparql-results+json"}
    headers['Authorization'] = atoken
    r = requests.get(repository, params={"query": query}, headers=headers)
    for res in json.loads(r.text)["results"]["bindings"]:
        yield res

repository = "ResQvirt"
sparql_query = """

PREFIX resqplus: <http://www.semanticweb.org/catimc/resqplus#>
PREFIX scdm: <http://www.semanticweb.org/catimc/SemanticCommonDataModel#>
PREFIX sct: <http://snomed.info/id/>
PREFIX btl2: <http://purl.org/biotop/btl2.owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
select * where {
    <http://resqplus-resources/ontologies/resqplus-data#Case_1> ?p ?checkTheField .
    ?checkTheField scdm:isResultOf ?procedure.
}


"""
for row in parse_data(repository, sparql_query):
    print(row)