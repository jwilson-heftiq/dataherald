import requests
import json
import sqlparse
from typing import List, Tuple, Set
import urllib.parse
import random
import argparse
from datetime import datetime
from itertools import product
from collections import defaultdict
import os
import boto3
from botocore.exceptions import ClientError

URI = "http://localhost:80/api/v1/"
S3_BUCKET = "k2-benchmark-results"

def add_context(samples: List[dict]):
    """Add context to the benchmark tool."""
    response = requests.post(URI + "golden-record", json=samples)
    print(f'Result of adding context: {response}')

def remove_distinct(query: str):
    toks = [t.value for t in list(sqlparse.parse(query)[0].flatten())]
    return ''.join([t for t in toks if t.lower() != 'distinct'])

def unorder_row(row: Tuple) -> Tuple:
    return tuple(sorted(row, key=lambda x: str(x) + str(type(x))))

def quick_rej(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    s1 = [unorder_row(row) for row in result1]
    s2 = [unorder_row(row) for row in result2]
    if order_matters:
        return s1 == s2
    else:
        return set(s1) == set(s2)

def get_constraint_permutation(tab1_sets_by_columns: List[Set], result2: List[Tuple]):
    num_cols = len(result2[0])
    perm_constraints = [{i for i in range(num_cols)} for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*perm_constraints)

    # we sample 20 rows and constrain the space of permutations
    for _ in range(20):
        random_tab2_row = random.choice(result2)

        for tab1_col in range(num_cols):
            for tab2_col in set(perm_constraints[tab1_col]):
                if random_tab2_row[tab2_col] not in tab1_sets_by_columns[tab1_col]:
                    perm_constraints[tab1_col].remove(tab2_col)
    return product(*perm_constraints)

# return whether two bag of relations are equivalent
def multiset_eq(l1: List, l2: List) -> bool:
    if len(l1) != len(l2):
        return False
    d = defaultdict(int)
    for e in l1:
        d[e] = d[e] + 1
    for e in l2:
        d[e] = d[e] - 1
        if d[e] < 0:
            return False
    return True

def permute_tuple(element: Tuple, perm: Tuple) -> Tuple:
    assert len(element) == len(perm)
    return tuple([element[i] for i in perm])

def postprocess(query: str) -> str:
    query = query.replace('> =', '>=').replace('< =', '<=').replace('! =', '!=')
    return query

def result_eq(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    if len(result1) == 0 and len(result2) == 0:
        return True

    # if length is not the same, then they are definitely different bag of rows
    if len(result1) != len(result2):
        return False

    num_cols = len(result1[0])
    # if the results do not have the same number of columns, they are different
    if len(result2[0]) != num_cols:
        return False

    # unorder each row and compare whether the denotation is the same
    # this can already find most pair of denotations that are different
    if not quick_rej(result1, result2, order_matters):
        return False

    # the rest of the problem is in fact more complicated than one might think
    # we want to find a permutation of column order and a permutation of row order,
    # s.t. result_1 is the same as result_2
    # we return true if we can find such column & row permutations
    # and false if we cannot
    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]

    # on a high level, we enumerate all possible column permutations that might make result_1 == result_2
    # we decrease the size of the column permutation space by the function get_constraint_permutation
    # if one of the permutation make result_1, result_2 equivalent, then they are equivalent
    for perm in get_constraint_permutation(tab1_sets_by_columns, result2):
        if len(perm) != len(set(perm)):
            continue
        if num_cols == 1:
            result2_perm = result2
        else:
            result2_perm = [permute_tuple(element, perm) for element in result2]
        if order_matters:
            if result1 == result2_perm:
                return True
        else:
            # in fact the first condition must hold if the second condition holds
            # but the first is way more efficient implementation-wise
            # and we use it to quickly reject impossible candidates
            if set(result1) == set(result2_perm) and multiset_eq(result1, result2_perm):
                return True
    return False

def run(db: str, sql: str) -> List[Tuple]:
    """Run the SQL query against the database."""
    payload = {
        "db_alias" : db,
        "sql_statement" : sql
    }
    try:
        response = requests.post(URI + "query", json=payload)
        response.raise_for_status()  # Raise an exception for non-2xx status codes
        result = response.json()[1]["result"]
        return result
    except requests.exceptions.RequestException as e:
        print("Error: An exception occurred during the request:", e)
        # Handle the request exception (connection errors, timeouts, etc.)
        return []
    except ValueError as ve:
        print("Error: Unable to parse JSON response:", ve)
        # Handle the JSON parsing error (invalid JSON format, missing keys, etc.)
        return []
    except KeyError as ke:
        print("Error: Missing 'result' key in the JSON response:", ke)
        # Handle the missing 'result' key error
        return [] 
    except Exception as ex:
        print("Error: An unexpected exception occurred:", ex)
        # Handle any other unexpected exception
        return [] 

def validate_response_object(test_dict: dict, response_dict: dict, keep_distinct: bool = False) -> dict:
    db = test_dict["db"]
    gold_sql = test_dict["sql"]
    sql_generated = response_dict["sql_query"]
    gold_sql, sql_generated = postprocess(gold_sql), postprocess(sql_generated)
    if not keep_distinct:
        gold_sql = remove_distinct(gold_sql)
        sql_generated = remove_distinct(sql_generated)
    order_matters = 'order by' in gold_sql.lower()
    p_denotation = run(db,sql_generated)
    g_denotation = run(db,gold_sql)
    equivalence = result_eq(g_denotation, p_denotation, order_matters=order_matters)
    if equivalence:
        label = "CORRECT"
    else:
        label = "WRONG"
    benchmark_result = {
        "question" : test_dict["nl_question"],
        "db" : db,
        "gold_sql" : gold_sql,
        "num_tockens_used" : response_dict["total_tokens"],
        "total_cost": response_dict["total_cost"],
        "sql_generated": sql_generated,
        "exec_time": response_dict["exec_time"],
        "status": label #fix
    }    
    return benchmark_result

def run_benchmark(tests: List[dict], output_file_name: str = "benchmark_results.jsonl"):
    """Run the benchmark tool."""
    with open(output_file_name, 'w') as out:
        for test in tests:
            payload = {
                "db_alias" : test["db"],
                "question" : test["nl_question"],
            }
            response = requests.post(URI + "question?" + urllib.parse.urlencode(payload))#, json=json.dumps(payload))
            jout = json.dumps(validate_response_object(test, response.json())) + '\n'
            out.write(jout)
            break

def upload_to_cloud(file_name: str, object_name, bucket: str = 'dataherald-benchmark-results'):
    """Upload the results to the cloud."""
    s3_client = boto3.client('s3')
    try:
        response = s3_client.upload_file(file_name, bucket, object_name)
    except ClientError as e:
        logging.error(e)
        return False
    return True

if __name__ == "__main__":
    """Run the benchmark tests."""
    resposne = requests.get(URI + "heartbeat")
    print("Running benchmark tests...")
    print(resposne.json())
    parser = argparse.ArgumentParser(description="The Dataherald Benchmark tool to test performance of text-to-SQL generation.",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-f", "--file", type=str, default="apps/ai/clients/benchmark-tool/test_suites/v2_real_estate.jsonl", help="The file containing the benchmark tests.")
    parser.add_argument("-u", "--upload", type=bool, default=False, help="Upload the results to the S3 bucket.")
    parser.add_argument("-o", "--output", type=str, default="apps/ai/clients/benchmark-tool/test_results/", help="The directory to save the benchmark results file")
    parser.add_argument("-p", "--percent", type=float, default=0.1, help="The percentage of the test set to use as context.")
    parser.add_argument("-s", "--size", type=float, default=1, help="What percent of the test suite to use in the test")
    args = parser.parse_args()
    config = vars(args)
    test_set_size = config["size"]
    with open(config["file"], "r") as f:
        json_list = list(f)
        tests = []
        for test in json_list:
            tests.append(json.loads(test))
        random.shuffle(tests)
        tests = tests[:int(len(tests) * test_set_size)]
        context_set =  tests[:int(len(tests) * test_set_size)]
        benchmark_set = tests[int(len(tests) * test_set_size):]
    
    output_file_name = f'{os.path.basename(config["file"])}-{datetime.now().strftime("%Y-%m-%d-%H-%M-%S")}.jsonl'
    output_file = f'{config["output"]}{output_file_name}'
    add_context(context_set)
    run_benchmark(benchmark_set, output_file)
    if config["upload"]:
        print(f"Uploading results of {len(benchmark_set)} to S3 bucket...")
        upload_to_cloud(output_file, output_file_name, S3_BUCKET)
    else:
        print(f"{len(benchmark_set)} results saved to {output_file_name}")