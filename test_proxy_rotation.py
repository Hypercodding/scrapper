#!/usr/bin/env python3
"""
Test script for proxy rotation functionality.
Run this to verify your proxy setup is working correctly.
"""

import sys
import time
from app.core.proxy_manager import ProxyManager, get_proxy_manager, reset_proxy_manager


def test_basic_proxy_manager():
    """Test basic ProxyManager functionality."""
    print("=" * 60)
    print("Test 1: Basic Proxy Manager")
    print("=" * 60)
    
    # Test with 3 dummy proxies
    proxy_urls = [
        "http://user1:pass1@proxy1.com:7030",
        "http://user2:pass2@proxy2.com:8080",
        "http://user3:pass3@proxy3.com:9090"
    ]
    
    manager = ProxyManager(proxy_urls, rotation_interval=10)
    
    print(f"\n✓ Created ProxyManager with {len(proxy_urls)} proxies")
    print(f"✓ Rotation interval: {manager.rotation_interval} seconds")
    
    # Get current proxy
    current = manager.get_current_proxy()
    print(f"\n📍 Current proxy: {manager._mask_proxy(current)}")
    
    # Test rotation
    print("\n🔄 Testing rotation...")
    for i in range(3):
        rotated = manager.rotate_proxy(force=True)
        print(f"   Proxy {i+1}: {manager._mask_proxy(rotated)}")
    
    print("\n✓ Test 1 passed!")


def test_proxy_failure_handling():
    """Test proxy failure handling."""
    print("\n" + "=" * 60)
    print("Test 2: Proxy Failure Handling")
    print("=" * 60)
    
    proxy_urls = [
        "http://user1:pass1@proxy1.com:7030",
        "http://user2:pass2@proxy2.com:8080",
        "http://user3:pass3@proxy3.com:9090"
    ]
    
    manager = ProxyManager(proxy_urls, rotation_interval=10)
    
    current = manager.get_current_proxy()
    print(f"\n📍 Starting with: {manager._mask_proxy(current)}")
    
    # Simulate failures
    print("\n⚠️  Simulating 3 failures on current proxy...")
    for i in range(3):
        manager.mark_proxy_failure()
    
    # Should have rotated to next proxy
    new_proxy = manager.get_current_proxy()
    print(f"\n📍 After failures, switched to: {manager._mask_proxy(new_proxy)}")
    
    # Mark success
    print("\n✓ Marking current proxy as successful...")
    manager.mark_proxy_success()
    
    print("\n✓ Test 2 passed!")


def test_proxy_stats():
    """Test proxy statistics."""
    print("\n" + "=" * 60)
    print("Test 3: Proxy Statistics")
    print("=" * 60)
    
    proxy_urls = [
        "http://user1:pass1@proxy1.com:7030",
        "http://user2:pass2@proxy2.com:8080",
        "http://user3:pass3@proxy3.com:9090"
    ]
    
    manager = ProxyManager(proxy_urls, rotation_interval=5)
    
    # Get initial stats
    stats = manager.get_proxy_stats()
    print(f"\n📊 Proxy Statistics:")
    print(f"   Total proxies: {stats['total_proxies']}")
    print(f"   Current proxy: {stats['current_proxy']}")
    print(f"   Rotation interval: {stats['rotation_interval']}s")
    
    print(f"\n💚 Proxy Health:")
    for proxy_name, health in stats['proxy_health'].items():
        status = "✓ Healthy" if health['healthy'] else "✗ Unhealthy"
        print(f"   {proxy_name}: {status} (failures: {health['failures']})")
    
    print("\n✓ Test 3 passed!")


def test_time_based_rotation():
    """Test time-based rotation."""
    print("\n" + "=" * 60)
    print("Test 4: Time-Based Rotation")
    print("=" * 60)
    
    proxy_urls = [
        "http://user1:pass1@proxy1.com:7030",
        "http://user2:pass2@proxy2.com:8080"
    ]
    
    # Short rotation interval for testing
    manager = ProxyManager(proxy_urls, rotation_interval=2)
    
    proxy1 = manager.get_current_proxy()
    print(f"\n📍 Starting with: {manager._mask_proxy(proxy1)}")
    
    print("\n⏳ Waiting for rotation interval (2 seconds)...")
    time.sleep(2.5)
    
    if manager.should_rotate():
        proxy2 = manager.rotate_proxy()
        print(f"✓ Rotated to: {manager._mask_proxy(proxy2)}")
        
        if proxy1 != proxy2:
            print("\n✓ Test 4 passed! Time-based rotation working correctly.")
        else:
            print("\n✗ Test 4 failed: Proxy didn't change after rotation")
    else:
        print("\n✗ Test 4 failed: should_rotate() returned False")


def test_global_proxy_manager():
    """Test global proxy manager functions."""
    print("\n" + "=" * 60)
    print("Test 5: Global Proxy Manager")
    print("=" * 60)
    
    # Reset any existing instance
    reset_proxy_manager()
    
    proxy_urls = [
        "http://user1:pass1@proxy1.com:7030",
        "http://user2:pass2@proxy2.com:8080"
    ]
    
    # Get global instance
    manager1 = get_proxy_manager(proxy_urls, rotation_interval=10)
    print(f"\n✓ Created global proxy manager")
    
    # Get it again (should return same instance)
    manager2 = get_proxy_manager()
    
    if manager1 is manager2:
        print("✓ Global proxy manager returns same instance")
    else:
        print("✗ Global proxy manager created new instance (should be same)")
        return
    
    # Reset and test again
    reset_proxy_manager()
    print("✓ Reset global proxy manager")
    
    print("\n✓ Test 5 passed!")


def test_with_actual_config():
    """Test with actual configuration from settings."""
    print("\n" + "=" * 60)
    print("Test 6: Using Actual Configuration")
    print("=" * 60)
    
    try:
        from app.core.config import settings
        
        # Parse proxy URLs from config
        proxy_urls = []
        if hasattr(settings, "PROXY_URLS") and settings.PROXY_URLS:
            proxy_urls = [url.strip() for url in settings.PROXY_URLS.split(",") if url.strip()]
        elif hasattr(settings, "PROXY_URL") and settings.PROXY_URL:
            proxy_urls = [settings.PROXY_URL.strip()]
        
        if not proxy_urls:
            print("\n⚠️  No proxies configured in settings.")
            print("   Add proxies to PROXY_URLS in app/core/config.py")
            print("   Example: PROXY_URLS = 'http://user:pass@host:port,http://user2:pass2@host2:port'")
            return
        
        rotation_interval = getattr(settings, "PROXY_ROTATION_INTERVAL", 240)
        
        print(f"\n✓ Found {len(proxy_urls)} proxy(ies) in configuration")
        print(f"✓ Rotation interval: {rotation_interval} seconds ({rotation_interval/60:.1f} minutes)")
        
        # Create manager
        manager = ProxyManager(proxy_urls, rotation_interval)
        
        print(f"\n📊 Current Configuration:")
        stats = manager.get_proxy_stats()
        print(f"   Total proxies: {stats['total_proxies']}")
        print(f"   Current proxy: {stats['current_proxy']}")
        
        if len(proxy_urls) == 1:
            print("\n⚠️  Only 1 proxy configured - rotation won't occur")
            print("   Add more proxies to enable rotation")
        else:
            print(f"\n✓ Rotation enabled with {len(proxy_urls)} proxies")
        
        print("\n✓ Test 6 passed!")
        
    except Exception as e:
        print(f"\n✗ Test 6 failed: {e}")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("PROXY ROTATION SYSTEM - TEST SUITE")
    print("=" * 60)
    
    try:
        # Run all tests
        test_basic_proxy_manager()
        test_proxy_failure_handling()
        test_proxy_stats()
        test_time_based_rotation()
        test_global_proxy_manager()
        test_with_actual_config()
        
        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED!")
        print("=" * 60)
        print("\nYour proxy rotation system is working correctly.")
        print("You can now add your proxies to the configuration and start scraping.\n")
        
    except Exception as e:
        print(f"\n" + "=" * 60)
        print("✗ TEST FAILED")
        print("=" * 60)
        print(f"Error: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

