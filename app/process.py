import subprocess
import time


def start_background_process(cmd):
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)  # Wait 2 seconds
    retcode = process.poll()
    if retcode is not None and retcode != 0:
        stdout, stderr = process.communicate()
        raise RuntimeError(f"Process failed with code {retcode}: {stderr.decode()}")
    return process  # Still running in background