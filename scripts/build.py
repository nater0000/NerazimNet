import PyInstaller.__main__
import os
import platform
import requests
import zipfile
import tarfile
import io
import logging
import toml
import shutil
import tempfile
import sys # Added for sys.platform check

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_config_value(key: str) -> str | None: # Return None if not found
    """Reads a value from the project's pyproject.toml file, located in the parent directory."""
    try:
        # --- CORRECTED PATH LOGIC ---
        script_dir = os.path.dirname(os.path.abspath(__file__)) # Directory of build.py (e.g., .../scripts)
        project_root = os.path.dirname(script_dir) # Parent directory (e.g., ...)
        toml_path = os.path.join(project_root, 'pyproject.toml') # Path to pyproject.toml in the root
        # --- END CORRECTION ---

        logging.debug(f"Attempting to read pyproject.toml from: {toml_path}") # Debug log
        with open(toml_path, 'r', encoding='utf-8') as f: # Specify encoding
            data = toml.load(f)
            # Navigate the TOML structure safely
            value = data.get('tool', {}).get('nerazimnet', {}).get(key)
            if value is None:
                logging.warning(f"Key '{key}' not found in [tool.nerazimnet] section of {toml_path}")
            return value
    except FileNotFoundError:
        logging.error(f"Failed to find pyproject.toml at {toml_path}")
        return None
    except KeyError as e:
        # This shouldn't happen with .get(), but kept for safety
        logging.error(f"Failed to read '{key}' structure from pyproject.toml: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred reading pyproject.toml: {e}", exc_info=True)
        return None


def _os_arch() -> tuple:
    """Returns (release_os_name, arch) for the current build platform."""
    mach = platform.machine().lower()
    arch = 'arm64' if mach in ('arm64', 'aarch64') else 'amd64'
    if sys.platform == 'win32':
        return 'windows', arch
    if sys.platform == 'darwin':
        return 'darwin', arch
    return 'linux', arch


def _exe_name(base: str) -> str:
    return base + ('.exe' if sys.platform == 'win32' else '')


def _fetch_binary(url: str, dest_dir: str, exe_name: str) -> bool:
    """Downloads a release archive (zip or tar.gz) and extracts exe_name
    plus any LICENSE/README files into dest_dir."""
    try:
        logging.info(f"Downloading from {url} ...")
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        logging.info("Download complete. Extracting...")
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, exe_name)

        with tempfile.TemporaryDirectory() as temp_dir:
            buf = io.BytesIO(response.content)
            if url.endswith('.zip'):
                with zipfile.ZipFile(buf) as z:
                    z.extractall(temp_dir)
            else:
                with tarfile.open(fileobj=buf, mode='r:gz') as t:
                    try:
                        t.extractall(temp_dir, filter='data')
                    except TypeError:
                        t.extractall(temp_dir) # Python <3.12 lacks filter=

            found = False
            for root, _dirs, files in os.walk(temp_dir):
                for name in files:
                    src = os.path.join(root, name)
                    if name == exe_name:
                        shutil.copy2(src, dest_path)
                        found = True
                    elif name.upper().startswith(('LICENSE', 'README')):
                        shutil.copy2(src, os.path.join(dest_dir, name))

        if not found or not os.path.exists(dest_path):
            logging.error(f"{exe_name} not found inside archive from {url}")
            return False
        if os.name != 'nt':
            os.chmod(dest_path, 0o755)
        logging.info(f"{exe_name} extracted successfully to {dest_path}.")
        return True

    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error during download: {http_err.response.status_code} - {http_err}")
        return False
    except requests.exceptions.RequestException as req_e:
        logging.error(f"Network error during download: {req_e}")
        return False
    except (zipfile.BadZipFile, tarfile.TarError) as arc_e:
        logging.error(f"Downloaded archive is invalid: {arc_e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error during download/extraction: {e}", exc_info=True)
        return False


def download_syncthing(version: str) -> bool:
    """Downloads the platform-appropriate Syncthing release into resources/syncthing."""
    if not version:
        logging.error("No Syncthing version provided. Aborting download.")
        return False

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    syncthing_dir = os.path.join(project_root, 'resources', 'syncthing')
    exe_name = _exe_name('syncthing')
    exe_path = os.path.join(syncthing_dir, exe_name)

    if os.path.exists(exe_path):
        logging.info(f"Syncthing executable already exists at {exe_path}. Skipping download.")
        return True

    os_name, arch = _os_arch()
    if os_name == 'darwin':
        os_name = 'macos' # Syncthing's asset naming
    ext = 'zip' if os_name in ('windows', 'macos') else 'tar.gz'
    url = f"https://github.com/syncthing/syncthing/releases/download/{version}/syncthing-{os_name}-{arch}-{version}.{ext}"

    return _fetch_binary(url, syncthing_dir, exe_name)


def download_frp(version: str) -> bool:
    """Downloads the platform-appropriate FRP release and extracts frpc into resources/frp."""
    if not version:
        logging.error("No FRP version provided. Aborting download.")
        return False

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frp_dir = os.path.join(project_root, 'resources', 'frp')
    exe_name = _exe_name('frpc')
    exe_path = os.path.join(frp_dir, exe_name)

    if os.path.exists(exe_path):
        logging.info(f"{exe_name} already exists at {exe_path}. Skipping download.")
        return True

    # GitHub tag is 'vX.Y.Z', archive name drops the 'v' prefix
    tag = version if version.startswith('v') else f"v{version}"
    plain_version = tag.lstrip('v')
    os_name, arch = _os_arch()
    ext = 'zip' if os_name == 'windows' else 'tar.gz'
    url = f"https://github.com/fatedier/frp/releases/download/{tag}/frp_{plain_version}_{os_name}_{arch}.{ext}"

    return _fetch_binary(url, frp_dir, exe_name)


if __name__ == '__main__':
    # Determine project root relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir) # Project root is parent of /scripts

    logging.info(f"Build script running from: {script_dir}")
    logging.info(f"Project Root determined as: {project_root}")

    # --- Construct source paths relative to project root ---
    syncthing_src_path_rel = os.path.join('resources', 'syncthing')
    frp_src_path_rel = os.path.join('resources', 'frp')
    images_src_path_rel = os.path.join('resources', 'images')
    themes_src_path_rel = os.path.join('resources', 'themes')
    main_script_path_rel = os.path.join('src', 'main.py')
    if sys.platform == 'darwin':
        icon_path_rel = os.path.join(images_src_path_rel, 'nerazimnet.icns')
    else:
        icon_path_rel = os.path.join(images_src_path_rel, 'nerazimnet.ico')
    
    # --- *** UPDATED PATH *** ---
    server_setup_path_rel = os.path.join('resources', 'server-setup') # Path relative to project root
    # --- *** END UPDATE *** ---

    # --- Construct absolute paths needed by PyInstaller ---
    main_script_abs = os.path.join(project_root, main_script_path_rel)
    icon_abs = os.path.join(project_root, icon_path_rel)

    # --- Verify essential source files/dirs exist before proceeding ---
    if not os.path.isfile(main_script_abs):
        logging.error(f"Main script not found at {main_script_abs}. Aborting build.")
        sys.exit(1)
    if not os.path.isdir(os.path.join(project_root, images_src_path_rel)):
         logging.warning(f"Images source directory not found at {os.path.join(project_root, images_src_path_rel)}. Icons might be missing.")
    if not os.path.isfile(icon_abs):
         logging.warning(f"Application icon not found at {icon_abs}. Default icon will be used.")

    # --- *** UPDATE DIRECTORY CHECK *** ---
    if not os.path.isdir(os.path.join(project_root, server_setup_path_rel)):
        logging.warning(f"Server setup template directory not found at {os.path.join(project_root, server_setup_path_rel)}. Provisioning might fail.")
    # --- *** END UPDATE *** ---

    # --- Read Syncthing version ---
    syncthing_version = get_config_value('syncthing_version')
    if not syncthing_version:
        logging.error("Build aborted: Syncthing version not found in pyproject.toml.")
        sys.exit(1)
    # --- Download Syncthing ---
    elif not download_syncthing(syncthing_version):
        logging.error("Build process aborted because Syncthing could not be downloaded/extracted.")
        sys.exit(1)
    # --- Read FRP version ---
    frp_version = get_config_value('frp_version')
    if not frp_version:
        logging.error("Build aborted: FRP version not found in pyproject.toml.")
        sys.exit(1)
    # --- Download FRP (frpc.exe client) ---
    elif not download_frp(frp_version):
        logging.error("Build process aborted because frpc.exe could not be downloaded/extracted.")
        sys.exit(1)
    # --- Binary Downloads Successful ---
    else:
        # --- Define PyInstaller arguments ---
        add_data_sep = os.pathsep # Use os-specific separator

        pyinstaller_args = [
            main_script_abs, # Use absolute path to main script
            '--name', 'NerazimNet',
            '--onefile',
            '--windowed', # No console window
            '--noconfirm', # Overwrite previous builds without asking
            '--clean', # Clean cache before build
            
            # --- Add Syncthing data ---
            '--add-data', f'{syncthing_src_path_rel}{add_data_sep}resources/syncthing',

            # --- Add FRP client (frpc.exe) ---
            '--add-data', f'{frp_src_path_rel}{add_data_sep}resources/frp',

            # --- Add Image data ---
            '--add-data', f'{images_src_path_rel}{add_data_sep}resources/images',

            # --- Add Theme data (nerazim.json) ---
            '--add-data', f'{themes_src_path_rel}{add_data_sep}resources/themes',

            # --- *** UPDATE TEMPLATE BUNDLING *** ---
            '--add-data', f'{server_setup_path_rel}{add_data_sep}resources/server-setup',
            # --- *** END UPDATE *** ---

            # --- Bundle pyproject.toml so version can be read at runtime ---
            '--add-data', f'pyproject.toml{add_data_sep}.',
        ]

        # --- Icon: .ico on Windows, .icns on macOS, unsupported on Linux ---
        if sys.platform != 'linux' and os.path.isfile(icon_abs):
            pyinstaller_args += ['--icon', icon_abs]
        if sys.platform == 'darwin':
            pyinstaller_args += ['--osx-bundle-identifier', 'com.nerazimnet.app']

        logging.info(f"Running PyInstaller with args: {' '.join(pyinstaller_args)}")

        # --- IMPORTANT: Change CWD to project root before running PyInstaller ---
        original_cwd = os.getcwd()
        try:
            os.chdir(project_root) # Change CWD to where pyproject.toml, resources, src are
            logging.info(f"Changed CWD to: {project_root} for PyInstaller")

            # --- Run PyInstaller ---
            PyInstaller.__main__.run(pyinstaller_args)
            logging.info("PyInstaller build complete.")

        except SystemExit as e:
             # PyInstaller often uses SystemExit on completion/error
             if e.code == 0:
                 logging.info("PyInstaller exited successfully.")
             else:
                 logging.error(f"PyInstaller exited with error code {e.code}.")
                 sys.exit(e.code) # Propagate error code
        except Exception as build_e:
            logging.error(f"PyInstaller build failed with an unexpected error: {build_e}", exc_info=True)
            sys.exit(1) # Exit with error code
        finally:
            os.chdir(original_cwd) # Change back to original CWD
            logging.info(f"Restored CWD to: {original_cwd}")