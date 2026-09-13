import glob
import os
import time

class Logging:
    # logFileOpened is a variable that keeps track of the log file status. 
    # It is initialized as False to represent the log file wasn't open.
    def __init__(self):
        self.logFileOpened = False
        # The log filename is resolved once on the first call. Re-running the
        # glob on every line made each log call a full directory scan; after
        # the first open the recomputed name was always this same file.
        self.logFileName = None

    # `log_string` is a string that is the log message that will be written to the log file. 
    def log(self, log_string: str):
        logs_directory = os.getcwd() + "/logs"

        if not os.path.exists(logs_directory):
            os.mkdir(logs_directory)
        
        if self.logFileName is None:
            log_files = glob.glob(r"logs/log-*.txt")

            # The log file numbers are extracted from the filenames
            log_file_numbers = [int(file[9:-4]) for file in log_files]
            
            # If log_file_numbers list is empty, append the value 0 to it.
            # This ensures that the list always contains at least one value.
            if not log_file_numbers:
                log_file_numbers.append(0)
            
            self.logFileName = f"logs/log-{max(log_file_numbers) + 1}.txt"

        
        with open(self.logFileName, "a" if self.logFileOpened else "w", encoding="utf-8") as log_file:
            self.logFileOpened = True

            current_time = time.strftime("%Y.%m.%d-%H.%M.%S", time.localtime(time.time()))
            # UTF-8 so non-ASCII characters (e.g. the Discord card's middle-dot
            # separator) survive into the log file instead of becoming '?'.
            log_file.write(f"[{current_time}] {log_string}\n")