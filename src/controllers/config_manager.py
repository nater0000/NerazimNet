import os
import json
import uuid
import logging
import secrets
import tempfile
from datetime import datetime, timezone
from utils.crypto import CryptoManager
from utils.paths import get_app_data_dir
import diff_match_patch as dmp_module


def _atomic_write(path: str, data: bytes):
    """Write a file atomically via a temp file + os.replace.

    Everything here lives in the Syncthing-shared SyncData dir — a crash
    mid-write would corrupt local state AND propagate the torn file to
    every peer, so partial writes must never appear under the real name.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise


class ConfigManager:
    def __init__(self, app_controller):
        self.controller = app_controller
        self.sync_path = os.path.join(get_app_data_dir(), 'SyncData')
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
        self._divergent_files = set()
        self._divergence_notified = set()

        os.makedirs(self.history_dir, exist_ok=True)

    def unlock_with_password(self, password: str) -> tuple[bool, str | None]:
        if not os.path.exists(self.check_file):
            logging.info("First-time setup: creating new configuration and check file.")
            self._master_password = password
            check_data = "NerazimNetVerification".encode('utf-8')
            encrypted_check = self.crypto_manager.encrypt_data(check_data, self._master_password)
            _atomic_write(self.check_file, encrypted_check)
            
            recovery_key = self.crypto_manager.generate_recovery_key()
            self.crypto_manager.save_recovery_key(recovery_key, password)
            self.crypto_manager.save_recovery_wrap(password, recovery_key)

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

    def re_encrypt_with_new_password(self, old_key: str, new_password: str,
                                     recovery_key: str = None) -> bool:
        """Re-encrypts every stored secret from old_key to new_password.

        old_key is normally the current master password; in the recovery
        flow it is the recovery key that unwrapped the password. Either way,
        recovery_key (when provided) is used to refresh the recovery
        artifacts; otherwise it's recovered via get_recovery_key(old_key).
        """
        if not os.path.exists(self.check_file): return False
        try:
            with open(self.check_file, 'rb') as f:
                encrypted_data = f.read()
            new_encrypted_data = self.crypto_manager.re_encrypt_with_new_password(encrypted_data, old_key, new_password)
            if not new_encrypted_data:
                logging.error("Failed to re-encrypt verification file.")
                return False
            _atomic_write(self.check_file, new_encrypted_data)

            # Refresh recovery artifacts under the new password
            rk = recovery_key or self.crypto_manager.get_recovery_key(old_key)
            if rk:
                self.crypto_manager.save_recovery_key(rk, new_password)
                self.crypto_manager.save_recovery_wrap(new_password, rk)
            else:
                logging.warning("Could not refresh recovery artifacts during password change.")

            if os.path.exists(self.credentials_file):
                with open(self.credentials_file, 'rb') as f:
                    encrypted_creds = f.read()
                new_encrypted_creds = self.crypto_manager.re_encrypt_with_new_password(encrypted_creds, old_key, new_password)
                if new_encrypted_creds:
                    _atomic_write(self.credentials_file, new_encrypted_creds)
                else:
                    logging.warning("Failed to re-encrypt credentials file.")
            
            for filename in os.listdir(self.sync_path):
                if filename.endswith('.patch'):
                    path = os.path.join(self.sync_path, filename)
                    with open(path, 'rb') as f: encrypted_patch = f.read()
                    new_encrypted_patch = self.crypto_manager.re_encrypt_with_new_password(encrypted_patch, old_key, new_password)
                    if new_encrypted_patch:
                        _atomic_write(path, new_encrypted_patch)
            
            self._master_password = new_password
            logging.info("All files re-encrypted successfully.")
            return True
        except Exception as e:
            logging.error(f"Failed during re-encryption: {e}", exc_info=True)
            return False
            
    def recover_with_recovery_key(self, recovery_key: str) -> str | None:
        """Recovers the master password using the recovery key, or None."""
        if not recovery_key:
            return None
        return self.crypto_manager.unwrap_master_password(recovery_key)

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
        self._notify_divergence()

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
            patch_files = sorted(
                f for f in os.listdir(self.sync_path)
                if f.endswith(f'_{file_id}.patch'))
            reconstructed_content = self._apply_patch_chain(patch_files, file_id)
            try:
                state[file_id] = json.loads(reconstructed_content)
            except json.JSONDecodeError:
                logging.error(f"Failed to decode reconstructed JSON for {file_id}")
        return state

    def _apply_patch_chain(self, patch_files: list, file_id: str) -> str:
        """Apply a file's decrypted patches in order, stopping at divergence.

        diff-match-patch applies hunks best-effort and returns a per-hunk
        flag list. A False flag means the patch was authored against a
        different base — almost always concurrent offline edits synced from
        another device. Continuing past that point compounds corruption, so
        the chain is truncated at the fork. This is deterministic: every
        replica sees the same patch files and truncates at the same point,
        so all devices reconverge to identical state.
        """
        content = ""
        for patch_file in patch_files:
            path = os.path.join(self.sync_path, patch_file)
            try:
                with open(path, 'rb') as f:
                    encrypted_patch = f.read()
                patch_text_bytes = self.crypto_manager.decrypt_data(
                    encrypted_patch, self._master_password)
                if not patch_text_bytes:
                    continue
                patches = self.dmp.patch_fromText(patch_text_bytes.decode('utf-8'))
                new_content, results = self.dmp.patch_apply(patches, content)
            except (ValueError, UnicodeDecodeError, OSError) as e:
                logging.error(f"Failed to apply patch {patch_file}: {e}")
                self._divergent_files.add(file_id)
                break
            if not all(results):
                failed = [i for i, ok in enumerate(results) if not ok]
                logging.warning(
                    f"Patch {patch_file} did not apply cleanly (failed hunks: "
                    f"{failed}) — history diverges here; keeping last "
                    f"consistent state for object {file_id}.")
                self._divergent_files.add(file_id)
                break
            content = new_content
        return content

    def get_divergent_files(self) -> dict:
        """file_id -> index entry for objects whose history diverged."""
        return {fid: self._file_index.get(fid, {"name": fid})
                for fid in self._divergent_files}

    def _notify_divergence(self):
        """Tell the controller once per newly-diverged file."""
        newly = self._divergent_files - self._divergence_notified
        if not newly:
            return
        self._divergence_notified |= newly
        if hasattr(self.controller, 'notify_config_divergence'):
            names = [self._file_index.get(fid, {}).get('name', fid)
                     for fid in sorted(newly)]
            try:
                self.controller.notify_config_divergence(names)
            except Exception as e:
                logging.error(f"Failed to notify divergence: {e}")

    def _commit_event(self, action: str, file_id: str, content_delta_text: str = None):
        timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-").replace("+00-00", "Z")
        manifest_delta = {'action': action, 'file_id': file_id, 'timestamp': timestamp}
        manifest_delta_path = os.path.join(self.history_dir, f"{timestamp}_{file_id}_manifest_delta.json")
        _atomic_write(manifest_delta_path, json.dumps(manifest_delta).encode('utf-8'))

        if content_delta_text is not None:
            encrypted_patch = self.crypto_manager.encrypt_data(content_delta_text.encode('utf-8'), self._master_password)
            patch_path = os.path.join(self.sync_path, f"{timestamp}_{file_id}.patch")
            _atomic_write(patch_path, encrypted_patch)

        self._prune_file_history(file_id)
        self.load_configuration()

    PATCH_KEEP = 20
    MANIFEST_KEEP = 100

    def _prune_file_history(self, file_id: str):
        """Caps per-file history so SyncData doesn't grow without bound.

        Patches form a chain from "", so the pruned prefix can't simply be
        deleted — it's first squashed into a single snapshot patch
        ("" -> content-at-boundary) reusing the oldest pruned filename,
        then the remaining pruned files are removed. Manifests get a longer
        leash since they're small and drive the History view timeline.
        """
        try:
            patches = sorted(
                f for f in os.listdir(self.sync_path)
                if f.endswith(f'_{file_id}.patch'))
            if len(patches) > self.PATCH_KEEP:
                # squash takes one slot, so the kept tail is PATCH_KEEP-1
                pruned = patches[:len(patches) - self.PATCH_KEEP + 1]
                boundary = self._apply_patch_chain(pruned, file_id)
                squash = self.dmp.patch_make("", boundary)
                squash_text = self.dmp.patch_toText(squash)
                encrypted = self.crypto_manager.encrypt_data(
                    squash_text.encode('utf-8'), self._master_password)
                # Reuse the oldest pruned filename — it still sorts first.
                squash_path = os.path.join(self.sync_path, pruned[0])
                _atomic_write(squash_path, encrypted)
                for stale in pruned[1:]:
                    os.remove(os.path.join(self.sync_path, stale))

            manifests = sorted(
                f for f in os.listdir(self.history_dir)
                if f.endswith(f'_{file_id}_manifest_delta.json'))
            for stale in manifests[:-self.MANIFEST_KEEP]:
                os.remove(os.path.join(self.history_dir, stale))
        except OSError as e:
            logging.warning(f"Failed to prune history for {file_id}: {e}")

    def add_object(self, obj_type: str, data: dict) -> str:
        obj_id = str(uuid.uuid4())
        data['id'] = obj_id
        data['type'] = obj_type
        
        if obj_type == 'server':
            data['is_provisioned'] = data.get('is_provisioned', False)

        name = data.get('name') or data.get('hostname') or obj_type
        self._file_index[obj_id] = {"name": name, "type": obj_type}
        _atomic_write(self.index_file, json.dumps(self._file_index, indent=2).encode('utf-8'))
        
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

    SETTINGS_ID = 'app-settings'

    def get_setting(self, key: str, default=None):
        obj = self._in_memory_state.get(self.SETTINGS_ID)
        return obj.get(key, default) if obj else default

    def set_setting(self, key: str, value):
        obj = self._in_memory_state.get(self.SETTINGS_ID)
        if obj is None:
            data = {'id': self.SETTINGS_ID, 'type': 'settings', key: value}
            self._file_index[self.SETTINGS_ID] = {'name': 'app-settings', 'type': 'settings'}
            _atomic_write(self.index_file, json.dumps(self._file_index, indent=2).encode('utf-8'))
            content_text = json.dumps(data, indent=2)
            patch_text = self.dmp.patch_toText(self.dmp.patch_make("", content_text))
            self._commit_event('add', self.SETTINGS_ID, patch_text)
        else:
            new_data = dict(obj)
            new_data[key] = value
            self.update_object(self.SETTINGS_ID, new_data)

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

    def save_or_update_automation_credentials(self, private_key_path: str, public_key_path: str,
                                              passphrase: str = None):
        logging.info("Saving automation credentials to dedicated file.")
        # Preserve any existing fields (e.g. frp_token) already stored in credentials.json
        new_data = dict(self._credentials or {})
        new_data.update({
            'ssh_private_key_path': private_key_path,
            'ssh_public_key_path': public_key_path
        })
        if passphrase:
            new_data['ssh_private_key_passphrase'] = passphrase
        # FRP requires an authorization token so rogue clients cannot bind ports on the VPS.
        if not new_data.get('frp_token'):
            new_data['frp_token'] = secrets.token_urlsafe(24) # 32-char random string
            logging.info("Generated new FRP auth token.")
        try:
            creds_bytes = json.dumps(new_data, indent=2).encode('utf-8')
            encrypted_creds = self.crypto_manager.encrypt_data(creds_bytes, self._master_password)
            _atomic_write(self.credentials_file, encrypted_creds)
            self._credentials = new_data
        except Exception as e:
            logging.error(f"Failed to save credentials file: {e}", exc_info=True)

    def ensure_ssh_key_encrypted(self):
        """Encrypts the app-managed SSH private key at rest, if still plaintext.

        The automation key grants SSH access (and via sudoers, root) on every
        provisioned VPS, so an unencrypted PEM on disk is a live credential.
        This re-encrypts it in place with a random passphrase and stores that
        passphrase inside the master-password-encrypted credentials.json.
        Only keys inside <app_data>/ssh_keys are touched — a user's own
        external key file is never rewritten.
        """
        creds = self._credentials
        if not creds:
            return
        key_path = creds.get('ssh_private_key_path')
        if not key_path or creds.get('ssh_private_key_passphrase'):
            return
        keys_dir = os.path.join(get_app_data_dir(), 'ssh_keys')
        try:
            if os.path.commonpath(
                    [os.path.abspath(key_path), os.path.abspath(keys_dir)]
                    ) != os.path.abspath(keys_dir):
                return  # user-supplied key outside our data dir — leave it alone
        except (ValueError, OSError):
            return
        if not os.path.exists(key_path):
            return
        passphrase = secrets.token_urlsafe(18)
        if self.crypto_manager.encrypt_ssh_private_key(key_path, passphrase):
            self.save_or_update_automation_credentials(
                key_path, creds.get('ssh_public_key_path') or '',
                passphrase=passphrase)
            logging.info("Encrypted the app-managed SSH private key at rest.")

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
            _atomic_write(self.credentials_file, encrypted_creds)
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
        eligible = []
        patch_files = sorted(
            f for f in os.listdir(self.sync_path)
            if f.endswith(f'_{file_id}.patch'))
        for patch_file in patch_files:
            try:
                timestamp_str = patch_file.split(f'_{file_id}.patch')[0]
                date_part, time_part = timestamp_str.split('T')
                time_part_fixed = time_part.replace('-', ':')
                iso_str = f"{date_part}T{time_part_fixed}".replace("Z", "+00:00")
                timestamp_dt = datetime.fromisoformat(iso_str)
                if timestamp_dt > target_timestamp:
                    break
                eligible.append(patch_file)
            except (ValueError, IndexError):
                logging.warning(f"Could not parse timestamp from {patch_file}")
                continue
        return self._apply_patch_chain(eligible, file_id)
    
    def is_configured(self) -> bool:
        return os.path.exists(self.check_file)

