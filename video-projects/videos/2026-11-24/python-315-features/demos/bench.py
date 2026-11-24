"""Start a script 30 times and print the median wall time."""
import statistics
import subprocess
import sys
import time

script = sys.argv[1]
times = []
for _ in range(30):
    start = time.perf_counter()
    subprocess.run([sys.executable, script], capture_output=True, check=True)
    times.append(time.perf_counter() - start)
print(f"{script}: median of 30 runs = {statistics.median(times) * 1000:.0f} ms")
