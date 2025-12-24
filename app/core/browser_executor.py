"""
Browser Lifecycle Management Module

This module provides centralized browser lifecycle management with guaranteed
hard process termination. Designed for Railway's constrained container environment.

Key Features:
- Hard process termination (OS-level kill)
- Guaranteed cleanup in all cases (success, error, crash, timeout)
- Process tracking to prevent zombie processes
- Resource cleanup verification
"""

import os
import time
import subprocess
import platform
from typing import Optional, List, Set
from contextlib import contextmanager
import logging

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logging.warning("psutil not available - using fallback process management")

logger = logging.getLogger(__name__)


class BrowserProcessManager:
    """
    Manages browser process lifecycle with hard termination guarantees.
    
    This class ensures that all Chrome/ChromeDriver processes are killed
    at the OS level, preventing resource leaks on Railway.
    """
    
    def __init__(self):
        self.tracked_pids: Set[int] = set()
        self.browser_process = None  # Track the main browser process
    
    def kill_all_chrome_processes(self) -> int:
        """
        Hard-kill ALL Chrome and ChromeDriver processes.
        
        This is aggressive but necessary for Railway's constrained environment.
        Returns the number of processes killed.
        """
        killed_count = 0
        current_pid = os.getpid()
        
        try:
            if PSUTIL_AVAILABLE:
                killed_count = self._kill_with_psutil(current_pid)
            else:
                killed_count = self._kill_with_subprocess(current_pid)
        except Exception as e:
            logger.error(f"Error during process cleanup: {e}")
        
        # Wait for system to release resources
        if killed_count > 0:
            time.sleep(2.0)
        
        return killed_count
    
    def _kill_with_psutil(self, current_pid: int) -> int:
        """Kill processes using psutil (preferred method)"""
        killed_count = 0
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                
                proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
                
                # Match Chrome/ChromeDriver processes used by Selenium
                is_chrome_process = (
                    ('chrome' in proc_name or 'chromedriver' in proc_name) and
                    ('--test-type' in cmdline or 
                     '--enable-automation' in cmdline or 
                     'chromedriver' in cmdline or
                     '--remote-debugging-port' in cmdline or
                     '--headless' in cmdline or
                     'selenium' in cmdline.lower())
                )
                
                if is_chrome_process:
                    logger.info(f"Killing browser process: {proc.info['name']} (PID: {proc.info['pid']})")
                    
                    try:
                        # Terminate gracefully first
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            # Force kill if terminate didn't work
                            logger.warning(f"Force killing process {proc.info['pid']}")
                            proc.kill()
                            proc.wait(timeout=2)
                        
                        killed_count += 1
                        self.tracked_pids.discard(proc.info['pid'])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        # Process already gone or permission denied
                        pass
                        
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        return killed_count
    
    def _kill_with_subprocess(self, current_pid: int) -> int:
        """Kill processes using subprocess (fallback method)"""
        killed_count = 0
        system = platform.system()
        
        if system not in ("Linux", "Darwin"):  # Linux or macOS
            logger.warning("Subprocess cleanup only supported on Linux/macOS")
            return 0
        
        try:
            # Find Chrome/ChromeDriver processes
            result = subprocess.run(
                ["pgrep", "-f", "chrome|chromedriver"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                pids = [int(pid) for pid in result.stdout.strip().split('\n') if pid.strip()]
                
                for pid in pids:
                    if pid == current_pid:
                        continue
                    
                    # Check if it's a selenium-related process
                    try:
                        cmdline_result = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "cmd="],
                            capture_output=True,
                            text=True,
                            timeout=2
                        )
                        
                        if cmdline_result.returncode == 0:
                            cmdline = cmdline_result.stdout.lower()
                            if any(keyword in cmdline for keyword in [
                                '--test-type', '--enable-automation', 'chromedriver',
                                '--remote-debugging-port', '--headless', 'selenium'
                            ]):
                                logger.info(f"Killing browser process (PID: {pid})")
                                
                                # Try SIGTERM first
                                subprocess.run(["kill", "-15", str(pid)], timeout=2)
                                time.sleep(1)
                                
                                # Check if still running, then force kill
                                check = subprocess.run(
                                    ["ps", "-p", str(pid)],
                                    capture_output=True,
                                    timeout=2
                                )
                                
                                if check.returncode == 0:
                                    logger.warning(f"Force killing process (PID: {pid})")
                                    subprocess.run(["kill", "-9", str(pid)], timeout=2)
                                
                                killed_count += 1
                                self.tracked_pids.discard(pid)
                    except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
                        pass
                        
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"Subprocess cleanup error: {e}")
        
        return killed_count
    
    def kill_browser_process_tree(self, pid: int) -> bool:
        """
        Kill a process and all its children (process tree).
        
        Useful for killing Chrome which spawns multiple child processes.
        """
        if not pid:
            return False
        
        try:
            if PSUTIL_AVAILABLE:
                return self._kill_tree_psutil(pid)
            else:
                return self._kill_tree_subprocess(pid)
        except Exception as e:
            logger.error(f"Error killing process tree for PID {pid}: {e}")
            return False
    
    def _kill_tree_psutil(self, pid: int) -> bool:
        """Kill process tree using psutil"""
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            
            # Kill children first
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            
            # Wait a bit for children to die
            gone, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            
            # Kill parent
            try:
                parent.terminate()
                parent.wait(timeout=3)
            except psutil.TimeoutExpired:
                parent.kill()
                parent.wait(timeout=2)
            except psutil.NoSuchProcess:
                pass
            
            return True
        except psutil.NoSuchProcess:
            return True  # Process already gone
        except psutil.AccessDenied:
            logger.error(f"Access denied when killing process {pid}")
            return False
    
    def _kill_tree_subprocess(self, pid: int) -> bool:
        """Kill process tree using subprocess (fallback)"""
        system = platform.system()
        if system not in ("Linux", "Darwin"):
            return False
        
        try:
            # Get all child PIDs
            result = subprocess.run(
                ["pgrep", "-P", str(pid)],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            child_pids = []
            if result.returncode == 0 and result.stdout.strip():
                child_pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
            
            # Kill children first (recursively)
            for child_pid in child_pids:
                self._kill_tree_subprocess(child_pid)
            
            # Kill parent
            subprocess.run(["kill", "-15", str(pid)], timeout=2)
            time.sleep(1)
            
            # Force kill if still running
            check = subprocess.run(["ps", "-p", str(pid)], capture_output=True, timeout=2)
            if check.returncode == 0:
                subprocess.run(["kill", "-9", str(pid)], timeout=2)
            
            return True
        except Exception:
            return False
    
    def verify_no_chrome_processes(self) -> bool:
        """Verify that no Chrome/ChromeDriver processes are running"""
        try:
            if PSUTIL_AVAILABLE:
                return self._verify_with_psutil()
            else:
                return self._verify_with_subprocess()
        except Exception:
            return False
    
    def _verify_with_psutil(self) -> bool:
        """Verify using psutil"""
        current_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                
                proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
                
                if (('chrome' in proc_name or 'chromedriver' in proc_name) and
                    ('--test-type' in cmdline or '--enable-automation' in cmdline or
                     'chromedriver' in cmdline or '--remote-debugging-port' in cmdline)):
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        return True
    
    def _verify_with_subprocess(self) -> bool:
        """Verify using subprocess"""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "chrome|chromedriver"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                current_pid = str(os.getpid())
                # Check if any process is not the current one
                return all(pid == current_pid for pid in pids if pid.strip())
            
            return True
        except Exception:
            return False


# Global instance
_process_manager = BrowserProcessManager()


def hard_kill_all_browsers() -> int:
    """
    Hard-kill all Chrome/ChromeDriver processes.
    
    This is the main cleanup function that should be called after every scrape.
    Returns the number of processes killed.
    """
    return _process_manager.kill_all_chrome_processes()


def verify_cleanup() -> bool:
    """Verify that no browser processes are running"""
    return _process_manager.verify_no_chrome_processes()


@contextmanager
def managed_browser(driver):
    """
    Context manager for browser lifecycle management.
    
    Ensures the browser is hard-killed even if an exception occurs.
    
    Usage:
        with managed_browser(driver) as browser:
            # Use browser here
            browser.get("https://example.com")
    """
    try:
        yield driver
    finally:
        # Always cleanup, regardless of success or failure
        cleanup_browser(driver)


def cleanup_browser(driver) -> None:
    """
    Comprehensive browser cleanup with hard process termination.
    
    This function:
    1. Attempts graceful quit
    2. Kills driver service process if still alive
    3. Hard-kills all Chrome processes via OS
    4. Verifies cleanup
    
    Args:
        driver: Selenium WebDriver instance (can be None)
    """
    if driver is None:
        logger.debug("No driver to cleanup")
        return
    
    # Step 1: Try graceful quit
    try:
        driver.quit()
        logger.debug("Driver quit gracefully")
    except Exception as e:
        logger.debug(f"Driver.quit() failed (expected in some cases): {e}")
    
    # Step 2: Kill service process if driver has one
    try:
        if hasattr(driver, 'service') and driver.service:
            if hasattr(driver.service, 'process') and driver.service.process:
                if driver.service.process.poll() is None:
                    logger.debug("Killing driver service process")
                    # Kill the entire process tree
                    pid = driver.service.process.pid
                    _process_manager.kill_browser_process_tree(pid)
    except Exception as e:
        logger.debug(f"Error killing service process: {e}")
    
    # Step 3: Hard-kill all Chrome processes (aggressive cleanup)
    killed = hard_kill_all_browsers()
    if killed > 0:
        logger.info(f"Hard-killed {killed} browser process(es)")
    
    # Step 4: Wait for system to release resources
    time.sleep(1.0)
    
    # Step 5: Verify cleanup (log warning if processes still exist)
    if not verify_cleanup():
        logger.warning("Warning: Some browser processes may still be running after cleanup")

