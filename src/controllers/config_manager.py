import os
import json
import uuid
import logging
import secrets
from datetime import datetime, timezone
from utils.crypto import CryptoManager
import diff_match_patch as dmp_module

class ConfigManager:
    def __init__(self, app_controller):
        self.controller = app_controller
        self.sync_path = os.path.join(os.getenv('APPDATA'), 'NerazimNet', 'SyncData')
        self.history_dir = os.path.join(self.sync_path, 'history')
        self.index_file = os.path.join(self.sync_path, '_index.json')
        self.check_file = os.path.join(self.sync_path, 'verification.dat')
        self.credentials_file = os.path.join(self.sync_path, 'credentials.json')
        
        self.crypto_manager = CryptoManager()
        self.dmp = dmp_module.diff_match_patch()
        
        self._master_password = None
        self._in_memory_state = {}
        self._file_index = {}
        self._credentials = None

        os.makedirs(self.history_dir, exist_ok=True)

    def unlock_with_password(self, password: str) -> tuple[bool, str | None]:
        if not os.path.exists(self.check_file):
            logging.info("First-time setup: creating new configuration and check file.")
            self._master_password = password
            check_data = "NerazimNetVerification".encode('utf-8')
            encrypted_check = self.crypto_manager.encrypt_data(check_data, self._master_password)
            with open(self.check_file, 'wb') as f:
                f.write(encrypted_check)
            
            recovery_key = self.crypto_manager.generate_recovery_key()
            self.crypto_manager.save_recovery_key(recovery_key, password)
            
            self.load_configuration()
            return True, recovery_key

        try:
            with open(self.check_file, 'rb') as f:
                encrypted_data = f.read()
            decrypted_data = self.crypto_manager.decrypt_data(encrypted_data, password)
            if decrypted_data and decrypted_data.decode('utf-8') == "NerazimNetVerification":
                self._master_password = password
                self.load_configuration()
                logging.info("Configuration successfully unlocked.")
                return True, None
            else:
                logging.warning("Password verification failed.")
                return False, None
        except Exception as e:
            logging.error(f"Failed to unlock configuration: {e}", exc_info=True)
            return False, None

    def re_encrypt_with_new_password(self, old_key: str, new_password: str) -> bool:
        if not os.path.exists(self.check_file): return False
        try:
            with open(self.check_file, 'rb') as f:
                encrypted_data = f.read()
            new_encrypted_data = self.crypto_manager.re_encrypt_with_new_password(encrypted_data, old_key, new_password)
            if not new_encrypted_data:
                logging.error("Failed to re-encrypt verification file.")
                return False
            with open(self.check_file, 'wb') as f: f.write(new_encrypted_data)

            if not self.crypto_manager.re_encrypt_recovery_key(old_key, new_password):
                 logging.error("Failed to re-encrypt recovery key.")

            if os.path.exists(self.credentials_file):
                with open(self.credentials_file, 'rb') as f:
                    encrypted_creds = f.read()
                new_encrypted_creds = self.crypto_manager.re_encrypt_with_new_password(encrypted_creds, old_key, new_password)
                if new_encrypted_creds:
                    with open(self.credentials_file, 'wb') as f:
                        f.write(new_encrypted_creds)
                else:
                    logging.warning("Failed to re-encrypt credentials file.")
            
            for filename in os.listdir(self.sync_path):
                if filename.endswith('.patch'):
                    path = os.path.join(self.sync_path, filename)
                    with open(path, 'rb') as f: encrypted_patch = f.read()
                    new_encrypted_patch = self.crypto_manager.re_encrypt_with_new_password(encrypted_patch, old_key, new_password)
                    if new_encrypted_patch:
                        with open(path, 'wb') as f: f.write(new_encrypted_patch)
            
            self._master_password = new_password
            logging.info("All files re-encrypted successfully.")
            return True
        except Exception as e:
            logging.error(f"Failed during re-encryption: {e}", exc_info=True)
            return False
            
    def get_recovery_key(self) -> str | None:
        if not self._master_password: return None
        return self.crypto_manager.get_recovery_key(self._master_password)

    def load_configuration(self):
        logging.info("Reconstructing configuration state from history...")
        if not self._master_password: return

        self._credentials = None
        if os.path.exists(self.credentials_file):
            try:
                with open(self.credentials_file, 'rb') as f:
                    encrypted_creds = f.read()
                decrypted_bytes = self.crypto_manager.decrypt_data(encrypted_creds, self._master_password)
                if decrypted_bytes:
                    self._credentials = json.loads(decrypted_bytes.decode('utf-8'))
            except Exception as e:
                logging.error(f"Failed to load or decrypt credentials file: {e}")

        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    self._file_index = json.load(f)
            except json.JSONDecodeError: self._file_index = {}
        
        history_files = sorted(os.listdir(self.history_dir))
        self._in_memory_state = self._reconstruct_state_from_events(history_files)
        logging.debug(f"Reconstructed state dump: {json.dumps(self._in_memory_state, indent=2)}")
        logging.info(f"Configuration loaded with {len(self._in_memory_state)} objects.")

    def _reconstruct_state_from_events(self, event_files):
        state = {}
        active_file_ids = set()
        for filename in event_files:
            if filename.endswith('_manifest_delta.json'):
                path = os.path.join(self.history_dir, filename)
                with open(path, 'r', encoding='utf-8') as f:
                    try:
                        delta = json.load(f)
                        if delta.get('action') == 'add': active_file_ids.add(delta.get('file_id'))
                        elif delta.get('action') == 'remove': active_file_ids.discard(delta.get('file_id'))
                    except (json.JSONDecodeError, KeyError): pass

        for file_id in active_file_ids:
            patch_files = sorted([f for f in os.listdir(self.sync_path) if file_id in f and f.endswith('.patch')])
            reconstructed_content = ""
            for patch_file in patch_files:
                path = os.path.join(self.sync_path, patch_file)
                with open(path, 'rb') as f: encrypted_patch = f.read()
                patch_text_bytes = self.crypto_manager.decrypt_data(encrypted_patch, self._master_password)
                if patch_text_bytes:
                    try:
                        patches = self.dmp.patch_fromText(patch_text_bytes.decode('utf-8'))
                        reconstructed_content, _ = self.dmp.patch_apply(patches, reconstructed_content)
                    except (ValueError, UnicodeDecodeError) as e:
                        logging.error(f"Failed to apply patch from {patch_file}: {e}")
            
            try:
                state[file_id] = json.loads(reconstructed_content)
            except json.JSONDecodeError:
                logging.error(f"Failed to decode reconstructed JSON for {file_id}")
        return state

    def _commit_event(self, action: str, file_id: str, content_delta_text: str = None):
        timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-").replace("+00-00", "Z")
        manifest_delta = {'action': action, 'file_id': file_id, 'timestamp': timestamp}
        manifest_delta_path = os.path.join(self.history_dir, f"{timestamp}_{file_id}_manifest_delta.json")
        with open(manifest_delta_path, 'w', encoding='utf-8') as f: json.dump(manifest_delta, f)

        if content_delta_text is not None:
            encrypted_patch = self.crypto_manager.encrypt_data(content_delta_text.encode('utf-8'), self._master_password)
            patch_path = os.path.join(self.sync_path, f"{timestamp}_{file_id}.patch")
            with open(patch_path, 'wb') as f: f.write(encrypted_patch)
        
        self.load_configuration()

    def add_object(self, obj_type: str, data: dict) -> str:
        obj_id = str(uuid.uuid4())
        data['id'] = obj_id
        data['type'] = obj_type
        
        if obj_type == 'server':
            data['is_provisioned'] = data.get('is_provisioned', False)

        name = data.get('name') or data.get('hostname') or obj_type
        self._file_index[obj_id] = {"name": name, "type": obj_type}
        with open(self.index_file, 'w', encoding='utf-8') as f: json.dump(self._file_index, f, indent=2)
        
        content_text = json.dumps(data, indent=2)
        patches = self.dmp.patch_make("", content_text)
        patch_text = self.dmp.patch_toText(patches)
        self._commit_event('add', obj_id, patch_text)
        return obj_id

    def update_object(self, obj_id: str, new_data: dict):
        if obj_id not in self._in_memory_state: return
        if 'type' not in new_data: new_data['type'] = self._in_memory_state[obj_id].get('type')
        if 'id' not in new_data: new_data['id'] = obj_id
        
        old_text = json.dumps(self._in_memory_state[obj_id], indent=2)
        new_text = json.dumps(new_data, indent=2)
        if old_text == new_text: return
        
        patches = self.dmp.patch_make(old_text, new_text)
        patch_text = self.dmp.patch_toText(patches)
        self._commit_event('update', obj_id, patch_text)
        
    def delete_object(self, obj_id: str):
        if obj_id not in self._in_memory_state: return
        self._commit_event('remove', obj_id)

    def get_all_objects_for_debug(self): return self._in_memory_state
    def get_object_by_id(self, obj_id: str): return self._in_memory_state.get(obj_id)

    def get_tunnels(self):
        tunnels = [obj for obj in self._in_memory_state.values() if obj.get('type') == 'tunnel' or ('hostname' in obj and not obj.get('type'))]
        return sorted(tunnels, key=lambda x: x.get('hostname', '').lower())

    def get_servers(self):
        servers = [obj for obj in self._in_memory_state.values() if obj.get('type') == 'server' or ('ip_address' in obj and not obj.get('type'))]
        return sorted(servers, key=lambda x: x.get('name', '').lower())
    
    def get_clients(self): 
        my_id = self.controller.get_my_device_id() or "" 
        clients = [obj for obj in self._in_memory_state.values() if obj.get('type') == 'client' and obj.get('syncthing_id') != my_id]
        return clients
    
    def get_server_name(self, server_id: str):
        server = self.get_object_by_id(server_id)
        return server.get('name', 'Unknown') if server else "Unknown"
        
    def get_client_name(self, client_id: str) -> str | None:
        if not client_id: return None
        my_id = self.controller.get_my_device_id()
        if my_id and client_id == my_id: return f"{self.controller.get_my_device_name()} (This Device)"
        for client in self.get_clients():
            if client.get('syncthing_id') == client_id: return client.get('name', client_id)
        return client_id[:12] + "..." if client_id else "Unknown"

    def get_automation_credentials(self):
        return self._credentials

    def save_or_update_automation_credentials(self, private_key_path: str, public_key_path: str):
        logging.info("Saving automation credentials to dedicated file.")
        # Preserve any existing fields (e.g. frp_token) already stored in credentials.json
        new_data = dict(self._credentials or {})
        new_data.update({
            'ssh_private_key_path': private_key_path,
            'ssh_public_key_path': public_key_path
        })
        # FRP requires an authorization token so rogue clients cannot bind ports on the VPS.
        if not new_data.get('frp_token'):
            new_data['frp_token'] = secrets.token_urlsafe(24) # 32-char random string
            logging.info("Generated new FRP auth token.")
        try:
            creds_bytes = json.dumps(new_data, indent=2).encode('utf-8')
            encrypted_creds = self.crypto_manager.encrypt_data(creds_bytes, self._master_password)
            with open(self.credentials_file, 'wb') as f:
                f.write(encrypted_creds)
            self._credentials = new_data
        except Exception as e:
            logging.error(f"Failed to save credentials file: {e}", exc_info=True)

    def get_or_create_frp_token(self) -> str | None:
        """Returns the FRP auth token, generating and persisting one if needed."""
        if not self._master_password:
            logging.error("Cannot get FRP token: configuration is locked.")
            return None
        creds = self._credentials
        if creds and creds.get('frp_token'):
            return creds['frp_token']
        try:
            new_data = dict(creds or {})
            new_data['frp_token'] = secrets.token_urlsafe(24)
            creds_bytes = json.dumps(new_data, indent=2).encode('utf-8')
            encrypted_creds = self.crypto_manager.encrypt_data(creds_bytes, self._master_password)
            with open(self.credentials_file, 'wb') as f:
                f.write(encrypted_creds)
            self._credentials = new_data
            logging.info("Generated and saved new FRP auth token.")
            return new_data['frp_token']
        except Exception as e:
            logging.error(f"Failed to generate FRP token: {e}", exc_info=True)
            return None
    
    def get_history_file_index(self):
        return [{"id": file_id, **data} for file_id, data in self._file_index.items()]

    def get_file_version_history(self, file_id: str) -> list:
        versions = []
        for filename in sorted(os.listdir(self.history_dir)):
            if file_id in filename and filename.endswith('_manifest_delta.json'):
                try:
                    path = os.path.join(self.history_dir, filename)
                    with open(path, 'r', encoding='utf-8') as f: delta = json.load(f)
                    raw_timestamp = delta['timestamp']
                    date_part, time_part = raw_timestamp.split('T')
                    time_part_fixed = time_part.replace('-', ':')
                    iso_str = f"{date_part}T{time_part_fixed}".replace("Z", "+00:00")
                    versions.append({'action': delta['action'], 'timestamp': datetime.fromisoformat(iso_str)})
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logging.warning(f"Skipping corrupt manifest {filename}: {e}")
        return versions

    def get_file_content_at_version(self, file_id: str, target_timestamp: datetime) -> str:
        reconstructed_content = ""
        patch_files = sorted([f for f in os.listdir(self.sync_path) if file_id in f and f.endswith('.patch')])
        for patch_file in patch_files:
            try:
                timestamp_str = patch_file.split(f'_{file_id}.patch')[0]
                date_part, time_part = timestamp_str.split('T')
                time_part_fixed = time_part.replace('-', ':')
                iso_str = f"{date_part}T{time_part_fixed}".replace("Z", "+00:00")
                timestamp_dt = datetime.fromisoformat(iso_str)
                if timestamp_dt <= target_timestamp:
                    path = os.path.join(self.sync_path, patch_file)
                    with open(path, 'rb') as f: encrypted_patch = f.read()
                    patch_text_bytes = self.crypto_manager.decrypt_data(encrypted_patch, self._master_password)
                    if patch_text_bytes:
                        patches = self.dmp.patch_fromText(patch_text_bytes.decode('utf-8'))
                        reconstructed_content, _ = self.dmp.patch_apply(patches, reconstructed_content)
                else: break
            except (ValueError, IndexError):
                logging.warning(f"Could not parse timestamp from {patch_file}")
                continue
        return reconstructed_content
    
    def is_configured(self) -> bool:
        return os.path.exists(self.check_file)

