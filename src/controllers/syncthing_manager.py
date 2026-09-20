import subprocess
import logging
import os
import time
import threading
import xml.etree.ElementTree as ET
import sys # <-- Added import
import socket
from syncthing import Syncthing, SyncthingError # Assuming syncthing2 library renamed to syncthing
from utils.staging import stage_bundled_exe

class SyncthingManager:
    """
    Manages the embedded Syncthing process and its API for decentralized config syncing.
    """
    def __init__(self, controller):
        self.controller = controller
        self.process = None
        self.api_key = None
        self.api_client = None
        self.my_device_id = None
        self.is_running = False

        # --- CORRECTED PATH LOGIC FOR PACKAGED APP ---
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            # Running in a PyInstaller bundle
            # sys._MEIPASS is the root of the extracted files (e.g., _MEIxxxxx)
            base_path = sys._MEIPASS
            logging.info(f"SyncthingManager: Running packaged. Base path (_MEIPASS): {base_path}")
            bundled_exe = os.path.join(base_path, "resources", "syncthing", "syncthing.exe")
            # Stage to a stable path — _MEIPASS is a fresh temp dir each launch,
            # and Windows Firewall rules key on the exe path (repeat prompts).
            self.syncthing_exe_path = stage_bundled_exe(bundled_exe, "syncthing.exe")
        else:
            # Running as a normal script
            # Path relative to this file (src/controllers/syncthing_manager.py)
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(script_dir)) # controllers -> src -> project root
            logging.info(f"SyncthingManager: Running script. Project root: {project_root}")
            self.syncthing_exe_path = os.path.join(project_root, "resources", "syncthing", "syncthing.exe")
        # --- END CORRECTION ---

        logging.info(f"Syncthing executable path determined: {self.syncthing_exe_path}")

        self.app_data_path = os.path.join(os.getenv('APPDATA'), 'NerazimNet')
        self.sync_folder_path = os.path.join(self.app_data_path, 'SyncData')
        # Ensure SyncData exists for Syncthing's config/logs later
        try:
            os.makedirs(self.sync_folder_path, exist_ok=True)
        except OSError as e:
             # Log error but don't prevent initialization, start() will handle it
             logging.error(f"Could not create SyncData directory {self.sync_folder_path}: {e}")
        self.folder_label = "NerazimNetConfig" # Syncthing Folder Label

    def start(self, base_port=8385, max_retries=10) -> bool:
        """Starts the Syncthing process, finds an open port, and initializes the API client."""
        if self.is_running:
            logging.info("Syncthing already running.")
            return True

        # Check path existence *before* trying to run
        if not os.path.exists(self.syncthing_exe_path):
            logging.error(f"Syncthing executable NOT FOUND at expected location: {self.syncthing_exe_path}")
            # Raise specific error to be caught in app.py
            raise FileNotFoundError(f"Syncthing not found: {self.syncthing_exe_path}")

        # --- Clean up any orphaned background processes holding the database lock ---
        try:
            if sys.platform == 'win32':
                wmic_path = self.syncthing_exe_path.replace('\\', '\\\\')
                subprocess.run(f'wmic process where "ExecutablePath=\'{wmic_path}\'" call terminate', shell=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.run(["pkill", "-f", self.syncthing_exe_path], capture_output=True)
            time.sleep(1) # Give the OS a moment to release file locks
        except Exception as e:
            logging.debug(f"Zombie cleanup failed or skipped: {e}")

        current_port = base_port

        for i in range(max_retries):
            # --- Find a truly free port (dodging Windows Hyper-V reserved ranges and zombies) ---
            port_found = False
            while current_port < base_port + 200: # Scan ahead up to 200 ports
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(('127.0.0.1', current_port))
                        port_found = True
                        break
                    except OSError:
                        logging.debug(f"Port {current_port} is in use or reserved by Windows. Skipping...")
                        current_port += 1
            
            if not port_found:
                 raise RuntimeError("Could not find any available ports for Syncthing.")

            # Syncthing config/data path within AppData
            config_path = os.path.join(self.app_data_path, "syncthing_config")
            log_path = os.path.join(config_path, "syncthing.log")
            try:
                os.makedirs(config_path, exist_ok=True) # Ensure config dir exists
            except OSError as e:
                logging.error(f"Cannot create Syncthing config directory {config_path}: {e}")
                raise RuntimeError(f"Cannot create Syncthing config directory: {e}") from e

            # Command parts
            command_parts = [
                self.syncthing_exe_path,
                 '--home', config_path, # Use --home for config/data dir
                 '--gui-address', f'127.0.0.1:{current_port}',
                 "--no-browser", # Prevent Syncthing from opening a browser
                 "--no-restart", # Disable monitor process so we can catch crashes immediately
                 "--no-upgrade", # Disable background updates from stalling the API
                 '--logfile', log_path # Log file within config dir
            ]

            command_str = ' '.join(command_parts)
            logging.info(f"Attempting ({i+1}/{max_retries}) to start Syncthing on port {current_port}: {command_str}")

            try:
                # Use CREATE_NO_WINDOW to hide console on Windows
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

                # Start the process
                self.process = subprocess.Popen(
                    command_parts, shell=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, # Discard stdout to prevent buffer lockups
                    text=True, encoding='utf-8', errors='replace', # Decode output as text
                    creationflags=creationflags
                )

                # Wait briefly to see if it fails immediately
                time.sleep(2)
                exit_code = self.process.poll()

                if exit_code is not None: # Process exited
                    stdout_data, stderr_data = "", ""
                    try:
                        # Try to get output if process ended quickly
                        stdout_data, stderr_data = self.process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        logging.warning("Syncthing process ended but failed to get output quickly.")
                    except Exception as comm_e:
                        logging.warning(f"Error getting output from ended Syncthing process: {comm_e}")

                    logging.error(f"Syncthing exited immediately (code {exit_code}) on port {current_port}.")

                    # Syncthing redirects fatal errors to the log file when --logfile is used
                    if os.path.exists(log_path):
                        try:
                            with open(log_path, 'r', encoding='utf-8') as f:
                                last_lines = "".join(f.readlines()[-5:])
                                if last_lines:
                                    logging.error(f"Syncthing log output before exit:\n{last_lines.strip()}")
                                    stderr_data += f"\n{last_lines}" # Feed into the checks below
                        except Exception as log_err:
                            logging.warning(f"Could not read log file to determine crash reason: {log_err}")

                    if stdout_data: logging.error(f"Syncthing STDOUT:\n{stdout_data.strip()}")
                    if stderr_data: logging.error(f"Syncthing STDERR:\n{stderr_data.strip()}")

                    # Check stderr for common errors like port conflict
                    if "address already in use" in stderr_data or "bind:" in stderr_data or "FATAL: Listen" in stderr_data or "database is locked" in stderr_data.lower():
                        logging.warning(f"Port {current_port} is in use or Syncthing already running. Retrying...")
                        self.process = None # Clear process ref
                        current_port += 1
                        continue # Try next port
                    else:
                        # Other immediate failure
                        logging.warning(f"Syncthing failed unexpectedly on port {current_port}. Retrying...")
                        self.process = None # Clear process ref
                        current_port += 1
                        continue # Try next port

                # If poll() is None, process is running
                logging.info(f"Syncthing process appears to be running on port {current_port}. Initializing API client...")
                api_init_success = self._initialize_api_client(config_path, current_port) # Initialize API

                if api_init_success:
                    logging.info("Syncthing startup and API connection successful.")
                    return True # Successfully started and connected
                else:
                     # API init failed, stop the process and retry port
                     logging.error("Syncthing process started but API initialization failed. Trying next port.")
                     if self.process and self.process.poll() is None:
                         try: 
                             if sys.platform == 'win32':
                                 subprocess.run(["taskkill", "/F", "/PID", str(self.process.pid), "/T"], check=False, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                             else:
                                 self.process.terminate()
                                 self.process.wait(timeout=2)
                         except Exception: pass
                     self.process = None
                     current_port += 1
                     continue # Try next port

            except Exception as e:
                logging.error(f"Failed to launch Syncthing on port {current_port}: {e}", exc_info=True)
                # Ensure process is cleaned up if Popen failed partially
                if self.process and self.process.poll() is None:
                    try:
                        if sys.platform == 'win32':
                            subprocess.run(["taskkill", "/F", "/PID", str(self.process.pid), "/T"], check=False, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            self.process.kill()
                    except Exception: pass
                self.process = None
                current_port += 1
                continue # Try next port

        # Loop finished without success
        logging.error("Failed to start Syncthing after multiple port attempts.")
        self.is_running = False
        # Raise an error to be caught by app.py's starter thread
        raise RuntimeError("Syncthing could not be started. Check logs for port conflicts or other errors.")


    def _initialize_api_client(self, config_path, port) -> bool:
        """Initializes the Syncthing API client after the process starts."""
        try:
            config_xml_path = os.path.join(config_path, "config.xml")
            # Wait for config file to be created by Syncthing
            for attempt in range(15): # Wait up to 15 seconds
                if os.path.exists(config_xml_path):
                     logging.debug(f"config.xml found after {attempt+1} seconds.")
                     break
                if attempt == 5: # Log if taking longer
                     logging.info("Waiting for Syncthing to create config.xml...")
                time.sleep(1)
            else: # Loop finished without break
                raise FileNotFoundError("Syncthing config.xml was not created in time.")

            # Add delay before parsing, sometimes file exists but isn't fully written
            time.sleep(1.0)

            # Parse config to get API key
            is_https = False
            try:
                tree = ET.parse(config_xml_path)
                api_key_node = tree.getroot().find('.//gui/apikey')
                if api_key_node is None or not api_key_node.text:
                    raise ValueError("API key not found or empty in config.xml")
                self.api_key = api_key_node.text
                logging.debug("API key retrieved from config.xml.")

                # Check if TLS is enabled
                gui_node = tree.getroot().find('.//gui')
                if gui_node is not None and gui_node.get('tls') == 'true':
                    is_https = True
                    logging.debug("GUI TLS is enabled in config.xml. Using HTTPS.")
            except ET.ParseError as xml_err:
                logging.error(f"Failed to parse config.xml: {xml_err}")
                raise ValueError("Could not parse Syncthing config.xml.") from xml_err

            # Create the client first
            self.api_client = Syncthing(self.api_key, host="127.0.0.1", port=port, is_https=is_https, timeout=5.0) # Add timeout

            # Poll for API connection
            protocol = "https" if is_https else "http"
            logging.info(f"Attempting to connect to Syncthing API at {protocol}://127.0.0.1:{port}...")
            max_api_retries = 60
            for i in range(max_api_retries): # Wait up to 60s for DB migrations/defender scans
                try:
                    status = self.api_client.system.status() # Poll status
                    self.my_device_id = status.get('myID')
                    if not self.my_device_id:
                         raise ValueError("API connected but myID not found in status.")
                    logging.info(f"Syncthing API connected successfully. This device ID: {self.my_device_id}")

                    # --- Crucial callbacks and setup ---
                    self.is_running = True
                    self.controller.on_syncthing_id_ready() # Notify App
                    self.create_initial_share() # Ensure shared folder exists
                    # --- End Crucial ---
                    return True # Success!
                except SyncthingError as api_err:
                    # Check for connection errors specifically
                    if "http request error" in str(api_err).lower() or "connection refused" in str(api_err).lower():
                        logging.debug(f"Syncthing API not ready yet (attempt {i+1}/{max_api_retries}). Retrying in 1 second...")
                        time.sleep(1)
                    else:
                        logging.error(f"Unexpected Syncthing API error: {api_err}")
                        raise # It's a different API error, raise it
                except ConnectionError as conn_err: # Catch lower-level connection errors
                    logging.debug(f"Syncthing API connection failed (attempt {i+1}/{max_api_retries}): {conn_err}. Retrying...")
                    time.sleep(1)
                except Exception as e: # Catch any other errors during status check
                    logging.error(f"Unexpected error connecting to Syncthing API: {e}", exc_info=True)
                    # Don't retry indefinitely on unexpected errors
                    raise ConnectionError(f"Unexpected error connecting to API: {e}") from e


            # If the loop finishes without returning, connection failed
            raise ConnectionError("Could not connect to Syncthing API after multiple retries.")

        except Exception as e:
            logging.error(f"Failed to initialize Syncthing API client: {e}", exc_info=True)
            self.api_client = None # Ensure client is None on failure
            self.api_key = None
            self.my_device_id = None
            return False # Indicate failure

    def stop(self):
        """Stops the Syncthing process if it's running."""
        if self.is_running and self.api_client:
            logging.info("Shutting down Syncthing via API.")
            try:
                self.api_client.system.shutdown()
                if self.process:
                    try: self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired: pass
            except Exception as e:
                logging.debug(f"Syncthing API shutdown failed or timed out: {e}")

        if self.process and self.process.poll() is None:
            logging.info("Forcing Syncthing process termination.")
            try:
                if sys.platform == 'win32':
                    subprocess.run(["taskkill", "/F", "/PID", str(self.process.pid), "/T"], check=False, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                else:
                    self.process.terminate() # Ask nicely first
                    try: self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired: self.process.kill()
                logging.info("Syncthing process terminated.")
            except Exception as e:
                 logging.error(f"Error stopping Syncthing process forcefully: {e}")

        self.process = None # Clear process reference
        self.is_running = False
        self.api_client = None # Clear API client
        self.api_key = None
        self.my_device_id = None


    def create_initial_share(self):
        """Ensures the NerazimNetConfig folder is shared in Syncthing."""
        if not self.api_client or not self.is_running:
            logging.warning("Cannot create share, Syncthing API not connected.")
            return

        try:
            config = self.api_client.system.config()
            folders = config.get('folders', [])
            folder_exists = any(f.get('id') == self.folder_label for f in folders)

            if not folder_exists:
                logging.info(f"Syncthing folder '{self.folder_label}' not found. Creating and sharing...")
                # Ensure the local path exists before telling Syncthing about it
                os.makedirs(self.sync_folder_path, exist_ok=True)

                new_folder_config = {
                    'id': self.folder_label, # Use label as ID for simplicity here
                    'label': self.folder_label,
                    'path': self.sync_folder_path,
                    'type': 'sendreceive', # Standard type
                    'devices': [], # Initially shared with no devices
                    'rescanIntervalS': 60, # Check for changes every minute
                    'fsWatcherEnabled': True, # Use filesystem events if possible
                    'autoNormalize': True, # Handle filename normalization
                }
                # Append the new folder config
                config.setdefault('folders', []).append(new_folder_config)

                # Post the updated config back to Syncthing
                self.api_client.system.post_config(config)
                logging.info(f"Successfully added folder '{self.folder_label}' to Syncthing config.")
                # Syncthing might need a restart or API call to rescan/apply immediately
                try:
                     self.api_client.db.scan(folder=self.folder_label) # Trigger scan
                except Exception as scan_e:
                     logging.warning(f"Could not trigger initial scan for {self.folder_label}: {scan_e}")

            else:
                 logging.debug(f"Syncthing folder '{self.folder_label}' already exists.")

        except SyncthingError as e:
            logging.error(f"Syncthing API error during initial share creation: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"Unexpected error during initial share creation: {e}", exc_info=True)


    def generate_invite(self) -> str | None:
        """Generates an invite string containing device ID and folder label."""
        # Requires my_device_id to be set by _initialize_api_client
        if self.my_device_id:
            return f"{self.my_device_id}|{self.folder_label}"
        else:
            logging.warning("Cannot generate invite: Syncthing device ID not yet known.")
            return None

    def accept_invite(self, invite_string: str) -> bool:
        """Adds a device and shares the folder based on an invite string."""
        if not self.api_client or not self.is_running:
             logging.error("Cannot accept invite, Syncthing API not connected.")
             return False

        try:
            parts = invite_string.strip().split('|')
            if len(parts) != 2:
                raise ValueError("Invalid invite string format.")
            device_id, folder_id = parts[0], parts[1]

            if folder_id != self.folder_label:
                 raise ValueError(f"Invite is for an unknown folder '{folder_id}'. Expected '{self.folder_label}'.")

            logging.info(f"Accepting invite from device {device_id} for folder {folder_id}")
            config = self.api_client.system.config() # Get current config

            # 1. Add the device if it doesn't exist
            devices = config.setdefault('devices', [])
            device_exists = any(d.get('deviceID') == device_id for d in devices)
            if not device_exists:
                logging.info(f"Adding new device: {device_id}")
                devices.append({
                    'deviceID': device_id,
                    'name': f'Synced Device ({device_id[:7]}...)', # Default name
                    'introducer': False, # Typically false unless it's a dedicated introducer
                    'compression': 'metadata' # Default compression
                })
            else:
                 logging.debug(f"Device {device_id} already exists.")

            # 2. Share the specific folder with the device if not already shared
            folder_updated = False
            for folder in config.get('folders', []):
                if folder.get('id') == folder_id: # Match by ID (which we set to label)
                    folder_devices = folder.setdefault('devices', [])
                    device_shared = any(d.get('deviceID') == device_id for d in folder_devices)
                    if not device_shared:
                         logging.info(f"Sharing folder '{folder_id}' with device {device_id}")
                         folder_devices.append({'deviceID': device_id})
                         folder_updated = True
                    else:
                         logging.debug(f"Folder '{folder_id}' already shared with device {device_id}.")
                    break # Found the correct folder
            else:
                 # This should not happen if create_initial_share worked
                 logging.error(f"Could not find folder '{folder_id}' in config to share.")
                 return False

            # 3. Post config only if changes were made
            if not device_exists or folder_updated:
                 logging.info("Posting updated configuration to Syncthing.")
                 self.api_client.system.post_config(config)
                 return True
            else:
                 logging.info("No configuration changes needed to accept invite.")
                 return True

        except ValueError as e:
            logging.error(f"Failed to accept invite: {e}")
            return False
        except SyncthingError as e:
             logging.error(f"Syncthing API error accepting invite: {e}", exc_info=True)
             return False
        except Exception as e:
            logging.error(f"Unexpected error accepting invite: {e}", exc_info=True)
            return False


    def remove_device(self, device_id: str):
        """Removes a device and removes it from the shared folder."""
        if not self.api_client or not self.is_running:
             logging.error(f"Cannot remove device {device_id}, Syncthing API not connected.")
             # Raise error to indicate failure
             raise ConnectionError("Syncthing API not connected.")

        try:
            logging.info(f"Attempting to remove device {device_id} from Syncthing config.")
            config = self.api_client.system.config() # Get current config
            changes_made = False

            # 1. Remove device from the specific shared folder's device list
            for folder in config.get('folders', []):
                if folder.get('id') == self.folder_label: # Match by ID/Label
                    original_devices = folder.get('devices', [])
                    updated_devices = [d for d in original_devices if d.get('deviceID') != device_id]
                    if len(updated_devices) < len(original_devices):
                         logging.info(f"Removing device {device_id} from folder '{self.folder_label}'.")
                         folder['devices'] = updated_devices
                         changes_made = True
                    break # Found the folder

            # 2. Remove the device from the global device list
            original_global_devices = config.get('devices', [])
            updated_global_devices = [d for d in original_global_devices if d.get('deviceID') != device_id]
            if len(updated_global_devices) < len(original_global_devices):
                 logging.info(f"Removing device {device_id} from global device list.")
                 config['devices'] = updated_global_devices
                 changes_made = True

            # 3. Post config only if changes were actually made
            if changes_made:
                 logging.info(f"Posting updated configuration to remove device {device_id}.")
                 self.api_client.system.post_config(config)
                 logging.info(f"Successfully removed device {device_id} from config.")
            else:
                 logging.info(f"Device {device_id} not found in configuration, no changes made.")

        except SyncthingError as e:
            logging.error(f"Syncthing API error removing device {device_id}: {e}", exc_info=True)
            raise # Re-raise API errors for the caller (app.py) to handle
        except Exception as e:
            logging.error(f"Unexpected error removing device {device_id}: {e}", exc_info=True)
            raise # Re-raise unexpected errors


    def get_devices(self) -> list:
        """Gets the list of configured devices from Syncthing."""
        if not self.api_client or not self.is_running:
             logging.warning("Cannot get devices, Syncthing API not connected.")
             return []
        try:
            config = self.api_client.system.config()
            return config.get('devices', [])
        except Exception as e:
            logging.error(f"Failed to get Syncthing devices: {e}")
            return []