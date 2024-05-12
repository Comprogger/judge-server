import sys
import subprocess
import psutil
import time

TIME_LIMIT = 2

def run_test_case(input_data, compiled_code, language, memory_limit):
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
                return 'Memory limit exceeded'

            # Check if the process exceeds the time limit
            if time.time() - start_time > TIME_LIMIT:
                process.terminate()
                return 'Time limit exceeded'
            
            time.sleep(0.1)  # Check every 0.1 second

        stdout, stderr = process.communicate()
        return stdout.decode()
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    input_data = sys.argv[1]
    compiled_code = sys.argv[2]
    language = sys.argv[3]
    memory_limit = int(sys.argv[4])
    print(run_test_case(input_data, compiled_code, language, memory_limit))
