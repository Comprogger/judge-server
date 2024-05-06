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
from flask_socketio import SocketIO
import signal

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "http://localhost:5173"}})
cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

TIME_LIMIT = 2
DEFAULT_MEMORY_LIMIT_MB = 256
def run_test_case(input_data, compiled_code, language, result_queue, memory_limit):
    try:
        process_memory_limit = memory_limit * 1024 * 1024  # Convert MB to bytes

        if language == 'python':
            process = subprocess.Popen(['python'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        elif language == 'java':
            temp_dir, class_name = compiled_code
            process = subprocess.Popen(['java', '-classpath', temp_dir, class_name], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        elif language == 'cpp':
            process = subprocess.Popen([compiled_code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        process.stdin.write(input_data.encode())
        process.stdin.flush()
        
        start_time = time.time()
        while process.poll() is None:
            # Check memory usage of the process
            if psutil.Process(process.pid).memory_info().rss > process_memory_limit:
                process.terminate()
                result_queue.put('Memory limit exceeded')
                return

            # Check if the process exceeds the time limit
            if time.time() - start_time > TIME_LIMIT:
                process.terminate()
                result_queue.put('Time limit exceeded')
                return
            
            time.sleep(0.1)  # Check every 0.1 second

        stdout, stderr = process.communicate()
        result_queue.put(stdout.decode())
    except Exception as e:
        result_queue.put(str(e))
        
def execute_code(code, test_cases, language, memory_limit=None):
    memory_limit = memory_limit or DEFAULT_MEMORY_LIMIT_MB
    results = []
    compiled_code = None
    tle = False
    if language == 'python':
        compiled_code = compile(code, '<string>', 'exec')
    elif language in ['java', 'cpp']:
        compiled_code = compile_code(code, language)

    for test_case in test_cases:
        key = test_case['key']
        input_data = str(test_case['input'])
        expected_output = str(test_case['output']).replace('\r', '')
        if (tle == True):
            results.append({'key': key, 'status': {'description': 'Wrong Answer', 'id': 2}, 'stdout': 'Nothing', 'time': 0})
            continue
        result_queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=run_test_case, args=(input_data, compiled_code, language, result_queue, memory_limit))
        start_time = time.time()
        process.start()
        process.join(timeout=1)  # Set a timeout of 1 second for process execution
        if process.is_alive():
            process.terminate()  # Terminate the process if it exceeds 1 second
            result = 'Time limit exceeded'
        else:
            result = result_queue.get()

        execution_time = time.time() - start_time

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
            results.append({'key': key, 'status': status, 'stdout': result, 'time': execution_time})

    return results


def compile_code(code, language):
    if language == 'java':
        class_name = re.search(r'class (\w+)', code).group(1)
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
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
        with tempfile.NamedTemporaryFile(suffix=".cpp", dir="/tmp", delete=False) as cpp_file:
            cpp_file.write(code.encode())
            cpp_file_name = cpp_file.name
        compile_result = subprocess.run(
            ['g++', cpp_file_name, '-o', cpp_file_name[:-4]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if compile_result.returncode != 0:
            return compile_result.stderr.decode()
        return cpp_file_name[:-4]

def execute_python_code(compiled_code, input_data):
    original_stdin = sys.stdin
    sys.stdin = io.StringIO(input_data)
    original_stdout = sys.stdout
    sys.stdout = output_capture = io.StringIO()

    try:
        exec(compiled_code)
        output = output_capture.getvalue()
        return output
    except Exception as e:
        return str(e)
    finally:
        sys.stdin = original_stdin
        sys.stdout = original_stdout

def execute_java_code(compiled_code, input_data):
    temp_dir, class_name = compiled_code
    try:
        run_result = subprocess.Popen(
            ['java', '-classpath', temp_dir, class_name], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        output, _ = run_result.communicate(input=input_data.encode())
        return output.decode()
    except Exception as e:
        return str(e)

def execute_cpp_code(compiled_code, input_data):
    try:
        run_result = subprocess.Popen(
            [compiled_code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        output, _ = run_result.communicate(input=input_data.encode())
        return output.decode()
    except Exception as e:
        return str(e)

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