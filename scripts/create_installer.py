import logging
import toml
import subprocess
import shutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_inno_setup(script_path: str, version: str):
    """Runs the Inno Setup compiler to create the installer."""
    compiler_path = "ISCC.exe" # Assumes Inno Setup is in the system's PATH
    if not shutil.which(compiler_path):
        logging.error(f"Inno Setup Compiler ('{compiler_path}') not found in your system's PATH.")
        logging.error("Please install Inno Setup and add its directory to your PATH environment variable.")
        return False
    
    logging.info("Running Inno Setup Compiler...")
    # Pass the app version from pyproject.toml to the Inno Setup script
    command = [compiler_path, f'/DMyAppVersion={version}', script_path]
    
    try:
        subprocess.run(command, check=True)
        logging.info("Installer created successfully in the 'Output' directory.")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Inno Setup Compiler failed: {e}")
        return False
    except FileNotFoundError:
        logging.error(f"Could not run '{compiler_path}'. Is Inno Setup installed and in your PATH?")
        return False

if __name__ == '__main__':
    try:
        with open('pyproject.toml', 'r') as f:
            project_data = toml.load(f)
            app_version = project_data['project']['version']
            installer_script = project_data['tool']['nerazimnet']['installer_script']
    except (FileNotFoundError, KeyError) as e:
        logging.error(f"Failed to read configuration from pyproject.toml: {e}")
        app_version = None
        installer_script = None

    if app_version and installer_script:
        run_inno_setup(installer_script, app_version)