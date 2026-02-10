#!/usr/bin/env python3
"""
Test script to verify yt-dlp fixes for n-challenge and 403 errors.
This script tests the updated yt-dlp configuration with YouTube compatibility flags.
"""

import subprocess
import sys
import json

def test_ytdlp_version():
    """Check yt-dlp version"""
    print("=" * 60)
    print("Testing yt-dlp version...")
    print("=" * 60)
    try:
        result = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        print(f"yt-dlp version: {result.stdout.strip()}")
        return True
    except Exception as e:
        print(f"❌ Failed to get yt-dlp version: {e}")
        return False

def test_node_dependencies():
    """Check if @yt-dlp/ejs is installed"""
    print("\n" + "=" * 60)
    print("Testing Node.js dependencies...")
    print("=" * 60)
    try:
        result = subprocess.run(
            ["npm", "list", "-g", "@yt-dlp/ejs"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if "@yt-dlp/ejs" in result.stdout:
            print("✅ @yt-dlp/ejs is installed")
            return True
        else:
            print("❌ @yt-dlp/ejs is NOT installed")
            return False
    except Exception as e:
        print(f"❌ Failed to check npm packages: {e}")
        return False

def test_metadata_extraction(url):
    """Test metadata extraction with new flags"""
    print("\n" + "=" * 60)
    print(f"Testing metadata extraction for: {url}")
    print("=" * 60)
    try:
        cmd = [
            "yt-dlp",
            "--dump-json",
            "--flat-playlist",
            "--socket-timeout", "30",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "--extractor-args", "youtube:player_client=android,web",
            "--",
            url
        ]
        
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0 and result.stdout:
            metadata = json.loads(result.stdout)
            print(f"✅ Metadata extracted successfully")
            print(f"   Title: {metadata.get('title', 'N/A')}")
            print(f"   Duration: {metadata.get('duration', 'N/A')}s")
            print(f"   Live Status: {metadata.get('live_status', 'N/A')}")
            return True
        else:
            print(f"❌ Metadata extraction failed")
            print(f"   Return code: {result.returncode}")
            if result.stderr:
                print(f"   Error: {result.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ Metadata extraction timed out")
        return False
    except Exception as e:
        print(f"❌ Metadata extraction error: {e}")
        return False

def test_download_simulation(url):
    """Test download simulation (no actual download)"""
    print("\n" + "=" * 60)
    print(f"Testing download simulation for: {url}")
    print("=" * 60)
    try:
        cmd = [
            "yt-dlp",
            "--simulate",
            "--socket-timeout", "30",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "--extractor-args", "youtube:player_client=android,web",
            "--sleep-requests", "1",
            "-f", "bestaudio",
            "--",
            url
        ]
        
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            print("✅ Download simulation successful")
            # Check for n-challenge warnings
            if "n challenge solving failed" in result.stderr or "n challenge solving failed" in result.stdout:
                print("⚠️  WARNING: n-challenge solving failed detected")
                return False
            if "403" in result.stderr or "403" in result.stdout:
                print("⚠️  WARNING: HTTP 403 error detected")
                return False
            return True
        else:
            print(f"❌ Download simulation failed")
            print(f"   Return code: {result.returncode}")
            if result.stderr:
                print(f"   Error output:")
                for line in result.stderr.split('\n')[:20]:
                    if line.strip():
                        print(f"   {line}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ Download simulation timed out")
        return False
    except Exception as e:
        print(f"❌ Download simulation error: {e}")
        return False

def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("YT-DLP FIX VERIFICATION SCRIPT")
    print("=" * 60)
    
    # Test URL (the one that was failing)
    test_url = "https://www.youtube.com/watch?v=4TM4aO1Kkg4"
    
    results = []
    
    # Run tests
    results.append(("yt-dlp version", test_ytdlp_version()))
    results.append(("Node.js dependencies", test_node_dependencies()))
    results.append(("Metadata extraction", test_metadata_extraction(test_url)))
    results.append(("Download simulation", test_download_simulation(test_url)))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    total_passed = sum(1 for _, passed in results if passed)
    total_tests = len(results)
    print(f"\nTotal: {total_passed}/{total_tests} tests passed")
    
    if total_passed == total_tests:
        print("\n🎉 All tests passed! The yt-dlp fixes are working correctly.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Please review the output above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
