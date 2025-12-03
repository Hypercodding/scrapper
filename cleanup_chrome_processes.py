#!/usr/bin/env python3
"""
Manual cleanup utility for Chrome/ChromeDriver processes.
Use this if you're experiencing connection pool issues.

Usage:
    python cleanup_chrome_processes.py
"""

import sys
import os


def cleanup_using_psutil():
    """Clean up using psutil (recommended)."""
    try:
        import psutil
        current_pid = os.getpid()
        killed_count = 0
        processes_to_kill = []
        
        print("\n🔍 Scanning for Chrome/ChromeDriver processes...")
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'username']):
            try:
                # Skip current process
                if proc.info['pid'] == current_pid:
                    continue
                
                proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
                username = proc.info['username'] if proc.info['username'] else 'unknown'
                
                # Check if it's a Chrome or ChromeDriver process related to Selenium
                if ('chrome' in proc_name or 'chromedriver' in proc_name) and \
                   ('--test-type' in cmdline or '--enable-automation' in cmdline or 'chromedriver' in cmdline):
                    processes_to_kill.append({
                        'pid': proc.info['pid'],
                        'name': proc.info['name'],
                        'cmdline': cmdline[:100] + '...' if len(cmdline) > 100 else cmdline,
                        'process': proc
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        if not processes_to_kill:
            print("✅ No Selenium Chrome/ChromeDriver processes found!")
            return 0
        
        print(f"\n📋 Found {len(processes_to_kill)} process(es) to clean up:")
        for i, proc_info in enumerate(processes_to_kill, 1):
            print(f"\n  {i}. PID: {proc_info['pid']}")
            print(f"     Name: {proc_info['name']}")
            print(f"     Command: {proc_info['cmdline']}")
        
        response = input(f"\n⚠️  Kill these {len(processes_to_kill)} process(es)? (yes/no): ").lower()
        
        if response not in ['yes', 'y']:
            print("\n❌ Cleanup cancelled.")
            return 0
        
        print("\n🧹 Cleaning up processes...")
        for proc_info in processes_to_kill:
            try:
                proc = proc_info['process']
                proc.terminate()
                proc.wait(timeout=3)
                print(f"✅ Killed {proc_info['name']} (PID: {proc_info['pid']})")
                killed_count += 1
            except psutil.TimeoutExpired:
                # Force kill if terminate doesn't work
                try:
                    proc.kill()
                    print(f"✅ Force killed {proc_info['name']} (PID: {proc_info['pid']})")
                    killed_count += 1
                except Exception as e:
                    print(f"❌ Failed to kill {proc_info['name']} (PID: {proc_info['pid']}): {e}")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                print(f"⚠️  Could not kill {proc_info['name']} (PID: {proc_info['pid']}): {e}")
        
        print(f"\n✅ Cleanup complete! Killed {killed_count} process(es).")
        return killed_count
        
    except ImportError:
        return None


def cleanup_using_system_commands():
    """Fallback: Clean up using system commands."""
    import platform
    
    system = platform.system()
    
    if system == "Linux" or system == "Darwin":  # macOS is Darwin
        print("\n🔍 Searching for Chrome/ChromeDriver processes...")
        
        # List processes first
        os.system("ps aux | grep -E '(chrome|chromedriver)' | grep -v grep")
        
        response = input("\n⚠️  Kill all Chrome/ChromeDriver processes? (yes/no): ").lower()
        
        if response not in ['yes', 'y']:
            print("\n❌ Cleanup cancelled.")
            return 0
        
        print("\n🧹 Killing processes...")
        
        # Kill Chrome processes
        ret1 = os.system("pkill -f 'chrome.*--test-type'")
        ret2 = os.system("pkill -f 'chrome.*--enable-automation'")
        ret3 = os.system("pkill -f chromedriver")
        
        if ret1 == 0 or ret2 == 0 or ret3 == 0:
            print("✅ Processes killed successfully!")
        else:
            print("⚠️  No processes found or error occurred.")
        
        return 1
        
    elif system == "Windows":
        print("\n🔍 Searching for Chrome/ChromeDriver processes...")
        
        # List processes first
        os.system('tasklist /FI "IMAGENAME eq chrome.exe"')
        os.system('tasklist /FI "IMAGENAME eq chromedriver.exe"')
        
        response = input("\n⚠️  Kill all Chrome/ChromeDriver processes? (yes/no): ").lower()
        
        if response not in ['yes', 'y']:
            print("\n❌ Cleanup cancelled.")
            return 0
        
        print("\n🧹 Killing processes...")
        
        # Kill Chrome and ChromeDriver
        os.system('taskkill /F /IM chrome.exe')
        os.system('taskkill /F /IM chromedriver.exe')
        
        print("✅ Cleanup complete!")
        return 1
    
    else:
        print(f"❌ Unsupported operating system: {system}")
        return 0


def check_process_count():
    """Check how many Chrome processes are running."""
    try:
        import psutil
        count = 0
        automation_count = 0
        
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                cmdline = ' '.join(proc.info['cmdline']) if proc.info['cmdline'] else ''
                
                if 'chrome' in proc_name or 'chromedriver' in proc_name:
                    count += 1
                    if '--test-type' in cmdline or '--enable-automation' in cmdline or 'chromedriver' in cmdline:
                        automation_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        return count, automation_count
    except ImportError:
        return -1, -1


def main():
    """Main function."""
    print("="*70)
    print(" CHROME/CHROMEDRIVER PROCESS CLEANUP UTILITY")
    print("="*70)
    print("\nThis utility will help clean up stuck Chrome/ChromeDriver processes")
    print("that may be causing connection pool or session creation errors.")
    
    # Check current process count
    total, automation = check_process_count()
    if total >= 0:
        print(f"\n📊 Current Chrome processes: {total}")
        print(f"📊 Selenium/automation processes: {automation}")
        
        if automation == 0:
            print("\n✅ No Selenium Chrome processes detected!")
            print("   Connection pool issues may be caused by something else.")
            response = input("\nContinue anyway? (yes/no): ").lower()
            if response not in ['yes', 'y']:
                print("\n👋 Exiting.")
                return
    
    print("\n" + "-"*70)
    
    # Try psutil first (more precise)
    result = cleanup_using_psutil()
    
    if result is None:
        # psutil not available, fall back to system commands
        print("\n⚠️  psutil not installed. Using system commands instead.")
        print("   For better control, install psutil: pip install psutil\n")
        cleanup_using_system_commands()
    elif result == 0:
        print("\n✅ No cleanup needed or cleanup cancelled.")
    
    # Check final count
    total, automation = check_process_count()
    if total >= 0:
        print(f"\n📊 Final Chrome process count: {total}")
        print(f"📊 Final Selenium/automation count: {automation}")
    
    print("\n" + "="*70)
    print("✅ Cleanup utility finished!")
    print("="*70)
    print("\nIf you continue to experience issues:")
    print("  1. Restart your application")
    print("  2. Restart your computer")
    print("  3. Check system resource limits (ulimit -n)")
    print("  4. Ensure Chrome and ChromeDriver are compatible versions")
    print("\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Cleanup interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

