"""Expose failed tests as GitHub annotations without requiring log access."""
from pathlib import Path
import xml.etree.ElementTree as ET

for report in Path("test-results").glob("*.xml"):
    for case in ET.parse(report).iter("testcase"):
        for tag in ("failure", "error"):
            failure = case.find(tag)
            if failure is not None:
                message = (failure.text or failure.get("message", "Test failed"))[-2500:]
                message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error title={case.get('name', 'Test')}::{message}")
