import xml.etree.ElementTree as ET


def print_tcx_course_points(file_path):
    try:
        # Parse the TCX XML file
        tree = ET.parse(file_path)
        root = tree.getroot()

        # Automatically detect the XML namespace (TCX files heavily rely on this)
        # ElementTree tags look like '{http://garmin.com}TrainingCenterDatabase'
        namespace = ''
        if root.tag.startswith('{'):
            namespace = root.tag.split('}')[0] + '}'

        # Define the namespace prefix dictionary for the XPath search
        ns = {'ns': namespace.strip('{}')} if namespace else {}

        # Search for all CoursePoint elements anywhere in the document
        xpath_query = './/ns:CoursePoint' if namespace else './/CoursePoint'
        course_points = root.findall(xpath_query, ns)

        if not course_points:
            print("No <CoursePoint> elements found in this file.")
            return

        print(f"Found {len(course_points)} course points:\n")
        print(f"{'Name':<20} | {'Type':<12} | {'Latitude':<12} | {'Longitude':<12} | Notes")
        print("-" * 85)

        for cp in course_points:
            # Extract standard TCX child elements safely
            # Note: Ride With GPS usually populates Name, Type, and sometimes Notes
            name = cp.findtext(f'{namespace}Name', default='N/A')
            cp_type = cp.findtext(f'{namespace}PointType', default='N/A')
            notes = cp.findtext(f'{namespace}Notes', default='').strip()

            # Extract coordinates from the nested Position element
            lat = 'N/A'
            lon = 'N/A'
            position = cp.find(f'{namespace}Position', ns)
            if position is not None:
                lat = position.findtext(f'{namespace}LatitudeDegrees', default='N/A')
                lon = position.findtext(f'{namespace}LongitudeDegrees', default='N/A')

            # Print the extracted row
            print(f"{name:<20} | {cp_type:<12} | {lat:<12} | {lon:<12} | {notes}")

    except FileNotFoundError:
        print(f"Error: The file '{file_path}' could not be found.")
    except ET.ParseError:
        print("Error: Failed to parse XML. Check if the file is a valid TCX format.")


# Example usage:
# Replace 'route.tcx' with the path to your Ride With GPS TCX export
print_tcx_course_points('../data/Smith_route.tcx')
