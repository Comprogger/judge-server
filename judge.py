import sys
import io
import tempfile
import re
import logging
import time
import os
import json
import signal
import uuid
import subprocess
import firebase_admin
from firebase_admin import credentials, firestore
from io import StringIO
import psutil
import multiprocessing
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask import send_file
from flask import send_from_directory
import signal
import docker

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "http://localhost:5173"}})
cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

TIME_LIMIT = 2
DEFAULT_MEMORY_LIMIT_MB = 256

def run_test_case(input_file, compiled_code, language, result_queue, memory_limit, container, command, time_limit=TIME_LIMIT):
    try:
        # Convert memory limit from MB to KB
        memory_limit_kb = memory_limit * 1024

        # Define the commands to be run
        combined_cmd = f'/bin/bash -c "ulimit -v {memory_limit_kb}; exec 3>&1 4>&2; TIMEFORMAT=\\"%3R\\"; time_result=$( (time (cat {input_file} | timeout --foreground {time_limit} {command} >\\&3) 2>&4) 2>&1); exec 3>&- 4>&-; echo \\"Runtime: $time_result\\"; echo \\"Exit Status: $?\\"; cat {input_file} | timeout --foreground {time_limit} /usr/bin/time -v {command} > /app/worker/output.txt; echo \\"Exit Status: $?\\""'
        
        # Run the combined command
        combined_out = container.exec_run(combined_cmd, stdout=True, stderr=True).output.decode('utf-8')

        # Parse output
        lines = combined_out.split('\n')
        print('lines', lines)
        runtime = float(lines[0])
        print('runtime', runtime)
        memory = float(lines[12].split(":")[1].strip()) / 1024
        print('memory', memory)
        exit_status1 = int(lines[-3].split(":")[1].strip())
        print('exit_status1`', exit_status1)        
        exit_status2 = int(lines[-2].split(":")[1].strip())
        print('exit_status2`', exit_status2)

        # Check the exit status and update the result queue
        if exit_status1 == 124 or exit_status2 == 124:
            result_queue.put(('Time limit exceeded', runtime, memory))
        elif exit_status1 == 134 or exit_status2 == 124:
            result_queue.put(('Memory limit exceeded', runtime, memory))
        else:
            # Read the contents of output.txt
            read_cmd = '/bin/bash -c "cat /app/worker/output.txt"'
            output_txt = container.exec_run(read_cmd).output.decode('utf-8')
            print("Output.txt:")
            print(output_txt)

            result_queue.put((output_txt, runtime, memory))
    except Exception as e:
        print(str(e))
        result_queue.put((str(e), 0, 0))

def execute_code(code, test_cases, language, memory_limit=None):
    memory_limit = memory_limit or DEFAULT_MEMORY_LIMIT_MB
    results = []
    compiled_code = None
    tle = False

    print('Compiling code...')

    if language == 'python':
        with tempfile.NamedTemporaryFile(suffix=".py", dir="/app/worker/python", delete=False) as py_file:
            py_file.write(code.encode())
            compiled_code = py_file.name
        os.chmod(compiled_code, 0o777)
    elif language in ['java', 'cpp']:
        compiled_code = compile_code(code, language)

    print('Done compiling')

    print("Creating docker client...")
    client = docker.DockerClient(base_url='unix://var/run/docker.sock')
    print("Client created")

    # Write all the input data to files in a folder
    input_dir = f"/app/worker/{language}/input_data"
    os.makedirs(input_dir, exist_ok=True)
    input_files = []
    for i, test_case in enumerate(test_cases):
        input_data = str(test_case['input'])
        input_file = os.path.join(input_dir, f"input_{i}.txt")
        with open(input_file, "w") as f:
            f.write(input_data)
        input_files.append(input_file)

    # Build Docker image from Dockerfile in /app/worker/{language}
    print("Building Docker image...")
    client.images.build(path=f'/app/worker/{language}', tag='worker_image')
    image = 'worker_image'

    if language == 'python':
        command = f'python {compiled_code}'
    elif language == 'java':
        temp_dir, class_name = compiled_code
        command = f'java -classpath {temp_dir} {class_name}'
    elif language == 'cpp':
        command = f'{compiled_code}'

    # Create Docker container
    print("Creating Docker container...")
    container = client.containers.create(
        image=image,
        command='/bin/sh',
        stdin_open=True,
        detach=True,
        tty=True
    )

    # Start the Docker container
    print("Starting Docker container...")
    container.start()

    test_case_start = time.time()

    for i, test_case in enumerate(test_cases):
        key = test_case['key']
        input_file = input_files[i]
        expected_output = str(test_case['output']).replace('\r', '')
        if (tle == True):
            results.append({'key': key, 'status': {'description': 'Wrong Answer', 'id': 2}, 'stdout': 'Nothing', 'time': 0})
            continue
        result_queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=run_test_case, args=(input_file, compiled_code, language, result_queue, memory_limit, container, command))
        process.start()

        result, runtime, memory = result_queue.get()

        result = result.replace('\r', '')

        status = {'description': 'Accepted', 'id': 1} if result.strip() == expected_output.strip() else {'description': 'Wrong Answer', 'id': 2}
        if result == 'Time limit exceeded':
            status = {'description': 'Time Limit Exceeded', 'id': 4}
            # If time limit exceeded, set remaining test cases to "Nothing" and "Wrong Answer"
            results.append({'key': key, 'status': status, 'stdout': 'Nothing', 'time': 0})
            tle = True  # Exit loop for remaining test cases
        elif result == 'Memory limit exceeded':
            status = {'description': 'Memory Limit Exceeded', 'id': 6}
            # If memory limit exceeded, set remaining test cases to "Nothing" and "Wrong Answer"
            results.append({'key': key, 'status': status, 'stdout': 'Nothing', 'time': 0})
            tle = True  # Exit loop for remaining test cases
            break  # Exit loop for remaining test cases
        else:
            results.append({'key': key, 'status': status, 'stdout': result, 'time': runtime, 'memory': memory})

        os.remove(input_file)

    test_case_time = time.time() - test_case_start
    print('test_case_time', test_case_time)

    print("Removing container...")
    container.remove(force=True)

    print("Deleting temporary files...")
    os.remove(compiled_code)

    if language == 'cpp':
        os.remove(f'{compiled_code}.cpp')

    return results

def compile_code(code, language):
    if language == 'java':
        class_name = re.search(r'class (\w+)', code).group(1)
        with tempfile.TemporaryDirectory(dir="/app/worker/java") as temp_dir:
            java_file_name = os.path.join(temp_dir, f"{class_name}.java")
            with open(java_file_name, 'w') as java_file:
                java_file.write(code)
            compile_result = subprocess.run(
                ['javac', java_file_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if compile_result.returncode != 0:
                return compile_result.stderr.decode()
            return temp_dir, class_name
    elif language == 'cpp':
        with tempfile.NamedTemporaryFile(suffix=".cpp", dir="/app/worker/cpp", delete=False) as cpp_file:
            cpp_file.write(code.encode())
            cpp_file_name = cpp_file.name
        compile_result = subprocess.run(
            ['g++', '-O3', '-march=native', '-funroll-loops', cpp_file_name, '-o', cpp_file_name[:-4]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if compile_result.returncode != 0:
            return compile_result.stderr.decode()
        os.chmod(cpp_file_name[:-4], 0o777)
        return cpp_file_name[:-4]

@app.route('/get_data')
def get_data(id):
    # Example: Get data from Firestore
    doc_ref = db.collection('Requests').document(id)
    doc = doc_ref.get()
    if doc.exists:
        return jsonify(doc.to_dict())
    else:
        return 'Document does not exist', 404

def process_queue():
    while True:
        # Check if there are any new requests in the queue
        queue_files = os.listdir('queue')
        for file in queue_files:
            if file.endswith('.txt'):
                # Read the request from the queue file
                with open(os.path.join('queue', file), 'r') as f:
                    data = json.load(f)
                language = data['language']
                code = data['code']
                test_cases = data['test_cases']
                memory_limit = data.get('memory_limit', DEFAULT_MEMORY_LIMIT_MB)  # Use default if not provided
                # Process the request
                results = execute_code(code, test_cases, language, memory_limit=memory_limit)


                # Add "stop" to the results array
                results.append({'key': 'stop', 'status': {'description': 'Processing complete', 'id': 5}, 'stdout': '', 'time': 0})

                # Update Firestore document with results 
                request_id = file[:-4]  # Get the request ID from the file name
                doc_ref = db.collection('Results').document(request_id)
                doc_ref.set({'results': results})

                # Log a message indicating the request ID
                logging.info(f'Results for request {request_id} updated in Firestore')

                # Remove the request from the queue
                os.remove(os.path.join('queue', file))

        # Sleep for 1 second before checking the queue again
        time.sleep(1)
        
@app.route('/get_results/<path:filename>')
def get_result_file(filename):
    return send_from_directory('results', filename)

# Modify the /execute route to return the request ID
@app.route('/execute', methods=['POST'])
# Modify the /execute route to accept memory_limit parameter
@app.route('/execute', methods=['POST'])
def execute():
    try:
        data = request.get_json()

        language = data.get('language', 'python')
        code = data.get('code', '')
        test_cases = data.get('test_cases', [])
        memory_limit = data.get('memory_limit', DEFAULT_MEMORY_LIMIT_MB)  # Use default if not provided

        # Generate a unique request ID
        request_id = str(uuid.uuid4())

        # Write the request to a new file in the queue
        with open(os.path.join('queue', f'{request_id}.txt'), 'w') as f:
            json.dump({'language': language, 'code': code, 'test_cases': test_cases, 'memory_limit': memory_limit}, f)

        return jsonify({'request_id': request_id})
    except Exception as e:
        # Log the error
        logging.error(f"An error occurred: {e}")
        return jsonify({'error': 'An error occurred during code submission.'}), 500
@app.route('/get_results/<request_id>')
def get_results(request_id):
    try:
        with open(os.path.join('results', f'{request_id}.jsonl'), 'r') as f:
            results = [json.loads(line.strip()) for line in f]
        return jsonify(results)
    except FileNotFoundError:
        return jsonify({'error': 'Results not found'}), 404







if __name__ == '__main__':
    # Create the queue and results directories if they don't exist
    if not os.path.exists('queue'):
        os.makedirs('queue')
    if not os.path.exists('results'):
        os.makedirs('results')

    # Start the queue processing thread
    import threading
    threading.Thread(target=process_queue).start()

    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    app.run(host='0.0.0.0', port=5000)