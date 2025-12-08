#!/usr/bin/env python3
"""
Manual Chrome/ChromeDriver cleanup script.
Run this if you need to manually clean up zombie Chrome processes.

Usage:
    python cleanup_chrome.py              # Normal cleanup
    python cleanup_chrome.py --aggressive # Aggressive cleanup (kills all)
    python cleanup_chrome.py --check      # Just check process count
"""

import sys
import argparse


def main():
    parser = argparse.ArgumentParser(description='Clean up zombie Chrome/ChromeDriver processes')
    parser.add_argument('--aggressive', '-a', action='store_true',
                      help='Aggressively kill all Chrome/ChromeDriver processes')
    parser.add_argument('--check', '-c', action='store_true',
                      help='Just check process count without cleanup')
    args = parser.parse_args()
    
    try:
        # Import the cleanup functions from the service
        from app.services.indeed_selenium_service import (
            cleanup_zombie_processes,
            cleanup_global_driver,
            check_chrome_process_count
        )
        
        print("=" * 60)
        print("Chrome/ChromeDriver Cleanup Tool")
        print("=" * 60)
        print()
        
        # Check current process count
        print("📊 Checking current Chrome process count...")
        count = check_chrome_process_count()
        if count >= 0:
            print(f"   Found {count} Chrome/ChromeDriver processes")
            
            if count == 0:
                print("   ✓ No Chrome processes running - system is clean!")
                return 0
            elif count > 20:
                print(f"   ⚠️  WARNING: {count} processes detected! Resource exhaustion likely!")
            elif count > 10:
                print(f"   ⚠️  WARNING: {count} processes detected - consider cleanup")
        else:
            print("   ⚠️  Could not determine process count (psutil not available)")
        
        print()
        
        if args.check:
            print("Check complete (use without --check to run cleanup)")
            return 0
        
        # Clean up global driver first
        print("🧹 Cleaning up global driver instance...")
        cleanup_global_driver()
        print()
        
        # Run zombie cleanup
        print(f"🧹 Running zombie process cleanup ({'aggressive' if args.aggressive else 'normal'})...")
        killed = cleanup_zombie_processes(aggressive=args.aggressive)
        print()
        
        # Check again after cleanup
        print("📊 Checking process count after cleanup...")
        count_after = check_chrome_process_count()
        if count_after >= 0:
            print(f"   Found {count_after} Chrome/ChromeDriver processes")
            
            if count_after == 0:
                print("   ✓ All Chrome processes cleaned up!")
            elif count_after < count:
                print(f"   ✓ Reduced from {count} to {count_after} processes")
                if count_after > 5:
                    print(f"   ⚠️  Still {count_after} processes remaining")
                    print("   Try running with --aggressive flag")
            else:
                print("   ⚠️  No change in process count")
                print("   Some processes may be protected or system-owned")
        
        print()
        print("=" * 60)
        print("Cleanup complete!")
        print("=" * 60)
        
        return 0
        
    except ImportError as e:
        print(f"❌ Error: Could not import cleanup functions: {e}")
        print("   Make sure you're running this from the project root directory")
        return 1
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

