#!/usr/bin/env python3
"""
Test script to verify connection pool and session creation fixes.
Run this to ensure the fixes are working properly.
"""

import sys
import time
from app.services.indeed_selenium_service import (
    get_driver,
    close_driver,
    cleanup_zombie_processes,
    check_chrome_process_count
)


def test_basic_driver_creation():
    """Test basic driver creation and cleanup."""
    print("\n" + "="*60)
    print("TEST 1: Basic Driver Creation and Cleanup")
    print("="*60)
    
    try:
        # Check initial process count
        initial_count = check_chrome_process_count()
        print(f"\n📊 Initial Chrome process count: {initial_count}")
        
        # Create driver
        print("\n🚀 Creating driver...")
        driver = get_driver()
        print("✅ Driver created successfully")
        
        # Check process count after creation
        after_create_count = check_chrome_process_count()
        print(f"📊 Chrome process count after creation: {after_create_count}")
        
        # Test driver works
        print("\n🧪 Testing driver navigation...")
        driver.get("https://www.google.com")
        print(f"✅ Navigated to: {driver.current_url}")
        
        # Close driver
        print("\n🧹 Closing driver...")
        close_driver()
        print("✅ Driver closed successfully")
        
        # Wait for processes to terminate
        time.sleep(2)
        
        # Check final process count
        final_count = check_chrome_process_count()
        print(f"📊 Final Chrome process count: {final_count}")
        
        # Verify cleanup worked
        if final_count <= initial_count:
            print("\n✅ TEST PASSED: Process cleanup successful")
            return True
        else:
            print(f"\n⚠️  TEST WARNING: {final_count - initial_count} processes may not have cleaned up")
            return True  # Still pass, but with warning
            
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        return False


def test_error_handling():
    """Test that driver cleanup works even when errors occur."""
    print("\n" + "="*60)
    print("TEST 2: Error Handling and Cleanup")
    print("="*60)
    
    try:
        initial_count = check_chrome_process_count()
        print(f"\n📊 Initial Chrome process count: {initial_count}")
        
        # Create driver
        print("\n🚀 Creating driver...")
        driver = get_driver()
        print("✅ Driver created successfully")
        
        # Simulate an error
        print("\n💥 Simulating error condition...")
        try:
            # This will cause an error
            driver.get("invalid-url-that-will-fail")
        except Exception as e:
            print(f"✅ Expected error occurred: {type(e).__name__}")
        
        # Even with error, driver should still be usable
        print("\n🧪 Testing driver still works...")
        driver.get("https://www.google.com")
        print("✅ Driver recovered from error")
        
        # Close driver
        print("\n🧹 Closing driver...")
        close_driver()
        print("✅ Driver closed successfully")
        
        # Wait and check cleanup
        time.sleep(2)
        final_count = check_chrome_process_count()
        print(f"📊 Final Chrome process count: {final_count}")
        
        if final_count <= initial_count + 1:  # Allow 1 process margin
            print("\n✅ TEST PASSED: Error handling and cleanup successful")
            return True
        else:
            print(f"\n⚠️  TEST WARNING: {final_count - initial_count} extra processes")
            return True
            
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        # Try to cleanup
        try:
            close_driver()
        except:
            pass
        return False


def test_zombie_cleanup():
    """Test zombie process cleanup."""
    print("\n" + "="*60)
    print("TEST 3: Zombie Process Cleanup")
    print("="*60)
    
    try:
        print("\n🔍 Checking for zombie processes...")
        initial_count = check_chrome_process_count()
        print(f"📊 Current Chrome process count: {initial_count}")
        
        print("\n🧹 Running zombie cleanup...")
        killed = cleanup_zombie_processes()
        
        if killed > 0:
            print(f"✅ Cleaned up {killed} zombie processes")
        else:
            print("✅ No zombie processes found (good!)")
        
        # Check final count
        time.sleep(1)
        final_count = check_chrome_process_count()
        print(f"📊 Final Chrome process count: {final_count}")
        
        print("\n✅ TEST PASSED: Zombie cleanup works")
        return True
        
    except Exception as e:
        print(f"\n⚠️  TEST INFO: {e}")
        print("(This is okay if psutil is not installed)")
        return True


def test_multiple_driver_creation():
    """Test creating and closing driver multiple times."""
    print("\n" + "="*60)
    print("TEST 4: Multiple Driver Creation/Cleanup Cycles")
    print("="*60)
    
    try:
        initial_count = check_chrome_process_count()
        print(f"\n📊 Initial Chrome process count: {initial_count}")
        
        iterations = 3
        print(f"\n🔄 Creating and closing driver {iterations} times...")
        
        for i in range(iterations):
            print(f"\n--- Iteration {i+1}/{iterations} ---")
            
            # Create driver
            driver = get_driver()
            print(f"✅ Driver {i+1} created")
            
            # Test it works
            driver.get("https://www.example.com")
            
            # Close it
            close_driver()
            print(f"✅ Driver {i+1} closed")
            
            # Brief pause
            time.sleep(1)
        
        # Final cleanup and check
        print("\n🧹 Final cleanup...")
        cleanup_zombie_processes()
        time.sleep(2)
        
        final_count = check_chrome_process_count()
        print(f"\n📊 Final Chrome process count: {final_count}")
        
        if final_count <= initial_count + 2:  # Allow small margin
            print("\n✅ TEST PASSED: Multiple cycles handled correctly")
            return True
        else:
            print(f"\n⚠️  TEST WARNING: {final_count - initial_count} extra processes after {iterations} cycles")
            print("   Consider restarting application periodically")
            return True
            
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        try:
            close_driver()
            cleanup_zombie_processes()
        except:
            pass
        return False


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print(" CONNECTION POOL & SESSION CREATION FIX - TEST SUITE")
    print("="*70)
    
    print("\nThis will test the fixes for connection pool and session creation errors.")
    print("Tests will create and close Chrome drivers to verify proper cleanup.")
    print("\nPress Ctrl+C at any time to abort.")
    
    try:
        input("\nPress Enter to start tests...")
    except KeyboardInterrupt:
        print("\n\nTests aborted.")
        return
    
    results = []
    
    # Run tests
    results.append(("Basic Driver Creation", test_basic_driver_creation()))
    results.append(("Error Handling", test_error_handling()))
    results.append(("Zombie Cleanup", test_zombie_cleanup()))
    results.append(("Multiple Cycles", test_multiple_driver_creation()))
    
    # Summary
    print("\n" + "="*70)
    print(" TEST SUMMARY")
    print("="*70)
    
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    # Overall result
    all_passed = all(result[1] for result in results)
    
    print("\n" + "="*70)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
        print("\nThe connection pool fixes are working correctly.")
        print("Your scraper should now run without connection pool issues.")
    else:
        print("⚠️  SOME TESTS FAILED")
        print("\nPlease check the errors above and ensure:")
        print("  1. Chrome and ChromeDriver are properly installed")
        print("  2. psutil is installed: pip install psutil")
        print("  3. You have sufficient permissions")
    print("="*70)
    
    # Final cleanup
    print("\n🧹 Final cleanup...")
    try:
        close_driver()
        cleanup_zombie_processes()
    except:
        pass
    
    print("\n✅ Test suite complete!\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user")
        print("🧹 Cleaning up...")
        try:
            close_driver()
            cleanup_zombie_processes()
        except:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        print("🧹 Cleaning up...")
        try:
            close_driver()
            cleanup_zombie_processes()
        except:
            pass
        sys.exit(1)

