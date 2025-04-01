import os
import re
import csv
import math
import time
import concurrent.futures
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from geopy.distance import geodesic
from functools import partial
from colorama import Fore, Back, Style, init
from tqdm import tqdm

init(autoreset=True)


def validate_coordinates(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
        
        return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180
    except ValueError:
        return False

def validate_radius(radius):
    try:
        radius = float(radius)
        
        return 0.01 <= radius <= 10000
    except ValueError:
        return False

def compute_zoom(kilometers):
    meters = kilometers * 1000

    if meters <= 71:
        return 21

    if meters >= 1164888:
        return 7

    init_meters = 1165116
    ratio = init_meters / meters
    exponent = math.log2(ratio)
    zoom = round(6 + exponent)

    return zoom

def sanitize(text, max_length=100):
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', text)

    return cleaned[:max_length].strip()

def get_input(prompt, validation, error_message):
    while True:
        user_input = input(f'{Style.BRIGHT}{Fore.YELLOW}{prompt}{Fore.RESET}')
        
        if validation(user_input):
            return user_input

        print(f'{Style.BRIGHT}{Back.RED}{error_message}')

def get_unique_filename(prefix):
    index = 0

    while True:
        filename = f'locations/{prefix}{index}.csv'

        if not os.path.exists(filename):
            return filename

        index += 1

def read_csv(file):
    with open(file, 'r', encoding='utf-8') as f:
        return set(tuple(row) for row in csv.reader(f))

def write_csv(data, output_file):
    with open(output_file, 'w', newline='', encoding='utf-8') as out:
        csv.writer(out).writerows(data)

def spiral(width, height, limits, step):
    x_min, x_max = limits[0], width - limits[0]
    y_min, y_max = limits[1], height - limits[1]

    x, y = width // 2, height // 2

    yield x, y
    
    directions = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    direction = 0
    segment_length = 1

    while True:
        for _ in range(2):
            dx, dy = directions[direction]

            for _ in range(segment_length):
                x += dx * step
                y += dy * step
                
                if x < x_min or x > x_max or y < y_min or y > y_max:
                    return
                
                yield x, y

                if (dx > 0 and x >= x_max) or (dx < 0 and x <= x_min) or \
                   (dy > 0 and y >= y_max) or (dy < 0 and y <= y_min):
                    return
            
            direction = (direction + 1) % 4

        segment_length += 1

def load_browser(page, lat, lon, zoom):
    page.goto(f'https://www.google.com/maps/@{lat},{lon},{zoom}z', wait_until='commit', timeout=100000)
    page.wait_for_selector('canvas', timeout=10000)

    print(f'{Style.BRIGHT}{Fore.YELLOW}Disabling sidebar...')

    try:
        page.wait_for_timeout(3000)
        sidebar = page.query_selector('button[jsaction="navigationrail.more"]')
        sidebar.click()

        page.wait_for_timeout(3000)
        switch = page.query_selector('button[jsaction="settings.toggleSideBar"]')

        if switch.get_attribute('aria-checked') == 'true':
            switch.click()

        close = page.query_selector('button[jsaction="settings.close"]')
        close.click()
    except (PlaywrightTimeoutError, AttributeError) as e:
        print(f'{Style.BRIGHT}{Fore.RED}Error while disabling sidebar: {str(e)}')

    print(f'{Style.BRIGHT}{Fore.YELLOW}Removing navigations...')

    page.wait_for_timeout(3000)
    page.query_selector('#itamenu').evaluate('node => node.remove()')
    page.query_selector('#assistive-chips').evaluate('node => node.remove()')

    print(f'{Style.BRIGHT}{Fore.GREEN}Browser is ready!')

def scan_locations():
    os.makedirs('locations', exist_ok=True)

    lat = get_input('Enter latitude: ', lambda x: validate_coordinates(x, '0'), 'Invalid coordinates!')
    lon = get_input('Enter longitude: ', lambda x: validate_coordinates('0', x), 'Invalid coordinates!')
    radius = get_input('Enter radius (km, 0.01-10): ', validate_radius, 'Invalid radius!')

    lat, lon = float(lat), float(lon)
    radius = float(radius)
    zoom = compute_zoom(radius)

    print(f'{Style.BRIGHT}{Fore.GREEN}Entry data received!')

    building_count = 0

    with sync_playwright() as p:
        print(f'{Style.BRIGHT}{Fore.YELLOW}Opening Google Maps...')

        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        load_browser(page, lat, lon, zoom)

        bounding_box = page.query_selector('canvas').bounding_box()
        page.wait_for_timeout(3000)

        width = int(bounding_box['width'])
        height = int(bounding_box['height'])
        limits = [width // 2, height // 2]
        x, y = limits

        for _ in tqdm(range((x - 100) // zoom), desc='Scanning X limit', unit='step'):
            page.mouse.click(bounding_box['x'] + x, bounding_box['y'] + y, button='right')
            page.wait_for_timeout(1000)
            coords = None

            try:
                coords = page.query_selector('div[data-index="0"]').text_content()
            except (PlaywrightTimeoutError, AttributeError):
                break

            page.mouse.click(bounding_box['x'] + 5, bounding_box['y'] + 5)
            coords = coords.split(', ')
            coords = (float(coords[0]), float(coords[1]))
            distance = geodesic((lat, lon), coords).kilometers

            if distance > radius:
                break

            x -= zoom
            limits[0] -= zoom

        page.mouse.click(bounding_box['x'] + 5, bounding_box['y'] + 5)
        x = width // 2

        print(f'{Style.BRIGHT}{Fore.GREEN}X limit found!')

        for _ in tqdm(range((y - 120) // zoom), desc='Scanning Y limit', unit='step'):
            page.mouse.click(bounding_box['x'] + x, bounding_box['y'] + y, button='right')
            page.wait_for_timeout(1000)
            coords = None
            address = None

            try:
                coords = page.query_selector('div[data-index="0"]').text_content()
            except (PlaywrightTimeoutError, AttributeError):
                break

            page.mouse.click(bounding_box['x'] + 5, bounding_box['y'] + 5)
            coords = coords.split(', ')
            coords = (float(coords[0]), float(coords[1]))
            distance = geodesic((lat, lon), coords).kilometers

            if distance > radius:
                break

            y -= zoom
            limits[1] -= zoom

        print(f'{Style.BRIGHT}{Fore.GREEN}Y limit found!')

        filename = get_unique_filename('locations')

        with open(filename, 'w', encoding='utf-8') as f:
            writer = csv.writer(f)

            for x, y in tqdm(spiral(width, height, limits, 5), desc='Scanning area', unit='step'):
                max_retries = 3
                attempt = 0
                success = False
                coords = None
                address = None

                while attempt < max_retries and not success:
                    try:
                        page.mouse.click(x, y, button='right')
                        page.wait_for_timeout(1000)

                        if page.query_selector('div[role="application"]').evaluate('node => node.style.cursor == "pointer"'):
                            page.mouse.click(bounding_box['x'] + 5, bounding_box['y'] + 5)
                            
                            success = False
                            
                            break

                        coords = page.query_selector('div[data-index="0"]').text_content().split(', ')
                        page.query_selector('div[data-index="4"]').click()
                        success = True
                    except (PlaywrightTimeoutError, AttributeError) as e:
                        print(f'{Style.BRIGHT}{Fore.RED}Interaction error (attempt {attempt + 1}/{max_retries}): {str(e)}')
                        
                        attempt += 1

                        page.wait_for_timeout(3000)
                        load_browser(page, lat, lon, zoom)
                        
                        if attempt == max_retries:
                            print(f'{Style.BRIGHT}{Back.RED}Fatal interaction error at ({x}, {y}). Skipping...')
                       
                        continue

                if not success:
                    continue 

                page.wait_for_timeout(1000)

                try:
                    dialog = page.query_selector('div[aria-live="assertive"]').query_selector('div[role="dialog"]')
                    address = dialog.query_selector('button').text_content()
                except (PlaywrightTimeoutError, AttributeError) as e:
                    print(f'{Style.BRIGHT}{Fore.RED}Modal dialog not found: {str(e)}')

                try:
                    dialog = page.query_selector('div[role="main"]')
                    
                    if dialog.is_visible():
                        try:
                            address = dialog.query_selector('button[data-item-id="address"]').text_content()[1:]
                        except (PlaywrightTimeoutError, AttributeError):
                            pass

                        load_browser(page, lat, lon, zoom)
                except (PlaywrightTimeoutError, AttributeError):
                    pass

                if not address:
                    continue

                page.mouse.click(bounding_box['x'] + 5, bounding_box['y'] + 5)
                writer.writerow([address, coords[0], coords[1]])
                building_count += 1

        browser.close()

    print(f'\n{Style.BRIGHT}{Fore.GREEN}Found {building_count} buildings within {radius}km radius. Result saved in {filename}')
    print(f'\n{Style.BRIGHT}{Fore.YELLOW}Press Enter to exit...')
    input()

def filter_locations():
    os.makedirs('locations', exist_ok=True)

    help_message = Style.BRIGHT + Fore.YELLOW + \
    '''Available commands:
    
    Union [file1] [file2] - Create a file with all locations from both files
    Intersect [file1] [file2] - Create a file with locations common to both files
    Difference [file1] [file2] - Create a file with locations from the first file not in the second
    Unique [file] - Create a file with unique locations from the given file
    Sort [file] - Create a file with sorted locations from the given file
    Help - Display this message
    End - Stop filter mode
    '''

    print(help_message)

    while True:
        parts = input(f'{Style.BRIGHT}{Fore.YELLOW}> {Fore.RESET}').lower().split()

        if not parts:
            print(f'{Style.BRIGHT}{Back.RED}No command entered!')

            continue

        cmd = parts[0].lower()

        if cmd == 'end':
            break

        elif cmd == 'help':
            print(help_message)

        elif cmd in ['union', 'intersect', 'difference']:
            if len(parts) != 3:
                print(f'{Style.BRIGHT}{Back.RED}Command requires two file names!')

                continue

            file1, file2 = parts[1:]
            file1 = 'locations/' + file1
            file2 = 'locations/' + file2

            if not os.path.exists(file1) or not os.path.exists(file2):
                print(f'{Style.BRIGHT}{Back.RED}One or both files do not exist!')

                continue

            lines1 = read_csv(file1)
            lines2 = read_csv(file2)

            addresses1 = {row[0].strip().lower() for row in lines1 if row}
            addresses2 = {row[0].strip().lower() for row in lines2 if row}

            if cmd == 'union':
                result = {row for row in lines1 if row and row[0].strip().lower() in addresses1} | \
                         {row for row in lines2 if row and row[0].strip().lower() in addresses2}
                output_file = get_unique_filename('union_result_')

            elif cmd == 'intersect':
                common_addresses = addresses1.intersection(addresses2)
                result = {row for row in lines1 if row and row[0].strip().lower() in common_addresses} | \
                         {row for row in lines2 if row and row[0].strip().lower() in common_addresses}
                output_file = get_unique_filename('intersection_result_')

            elif cmd == 'difference':
                diff_addresses = addresses1.difference(addresses2)
                result = {row for row in lines1 if row and row[0].strip().lower() in diff_addresses}
                output_file = get_unique_filename('difference_result_')

            write_csv(result, output_file)

            print(f'{Style.BRIGHT}{Fore.GREEN}{cmd.capitalize()} operation completed! Result saved in {output_file}')

        elif cmd in ['unique', 'sort']:
            if len(parts) != 2:
                print(f'{Style.BRIGHT}{Back.RED}Command requires one file name!')

                continue

            file = 'locations/' + parts[1]

            if not os.path.exists(file):
                print(f'{Style.BRIGHT}{Back.RED}File does not exist!')

                continue

            lines = read_csv(file)

            if cmd == 'unique':
                seen_addresses = set()
                result = []

                for row in lines:
                    try:
                        address = row[0].strip().lower()
                    except IndexError:
                        continue

                    if address not in seen_addresses:
                        seen_addresses.add(address)
                        result.append(row)

                output_file = get_unique_filename('unique_result_')

            elif cmd == 'sort':
                result = sorted(lines, key=lambda x: x[0].lower())
                output_file = get_unique_filename('sort_result_')

            write_csv(result, output_file)

            print(f'{Style.BRIGHT}{Fore.GREEN}{cmd.capitalize()} operation completed! Result saved in {output_file}')

        else:
            print(f'{Style.BRIGHT}{Back.RED}Incorrect command!')

def capture_location(location):
    address, lat, lon = location
    sanitized_name = sanitize(address) 
    base_name = sanitized_name or f'location_{lat}_{lon}'
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            
            page.goto(f'https://www.google.com/maps/search/{lat},{lon}', timeout=100000)
            page.wait_for_selector('canvas', timeout=10000)
            
            page.wait_for_selector('div[role="main"] img', timeout=10000)
            page.click(img_selector, timeout=10000)

            index = 1
            screenshot_path = f'buildings/{base_name}.png'

            while os.path.exists(screenshot_path):
                screenshot_path = f'buildings/{base_name}_{index}.png'
                index += 1
            
            page.locator('canvas').screenshot(path=screenshot_path)
            
            context.close()
            browser.close()

            return 1         
    except KeyboardInterrupt as e:
        print(f'\n{Style.BRIGHT}{Back.RED}Failed to capture {address}')

        return 0

def find_images():
    while True:
        filename = input(f'{Style.BRIGHT}{Fore.YELLOW}Enter the name of the CSV file with locations: ')
        filename = 'locations/' + filename

        if not os.path.exists(filename):
            print(f'{Style.BRIGHT}{Back.RED}File does not exist!')

            continue

        break

    os.makedirs('buildings', exist_ok=True)

    with open(filename, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        locations = [row for row in reader]

    batch_size = 5
    total = len(locations)
    success_count = 0

    with tqdm(total=total, desc='Capturing locations...') as pbar:
        with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = {executor.submit(capture_location, loc): loc for loc in locations}
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()

                if result:
                    success_count += 1

                pbar.update(1)

    print(f'\n{Style.BRIGHT}{Fore.GREEN}Saved {success_count}/{total} images in /buildings folder')
    print(f'\n{Style.BRIGHT}{Fore.YELLOW}Press Enter to exit...')
    input()

def banner(active_module):
    os.system('cls' if os.name == 'nt' else 'clear')
    
    width = 56
    
    top = Style.BRIGHT + Fore.RED + '╔' + '═' * width + '╗\n'
    
    title = 'CORRUPTOR\'S MAPPING TOOL'.center(width)
    title_line = Fore.RED + '║' + Style.BRIGHT + title + '║\n'
    
    module_line = ''

    if active_module:
        module = active_module.center(width - 1)
        module_line = Fore.RED + '║' + Fore.CYAN + module + Fore.RED + '║\n'

    else:
        module_line = Fore.RED + '║' + ' ' * width + Fore.RED + '║\n'
    
    bottom = Fore.RED + '╚' + '═' * width + '╝' + Style.RESET_ALL + '\n'
    
    banner_content = top + title_line + module_line + bottom
    
    print(banner_content)


modules = {
    '1': {'func': scan_locations, 'name': '📍 SCAN MODE'},
    '2': {'func': filter_locations, 'name': '🔎 FILTER MODE'},
    '3': {'func': find_images, 'name': '📸 CAPTURE MODE'}
}

try:
    while True:
        banner(active_module='📋 MAIN MENU')

        for key in modules:
            print(f'{Style.BRIGHT}{Fore.GREEN}{key}. {Fore.RESET}{Back.GREEN}{modules[key]["name"]}')

        while True:
            choice = input(f'\n{Style.BRIGHT}{Fore.YELLOW}Module to run: {Fore.RESET}')

            if choice in modules:
                banner(active_module=modules[choice]['name'])
                modules[choice]['func']()

                break

            else:
                print(f'\n{Style.BRIGHT}{Fore.RED}Incorrect choice!')

            print(f'\n{Style.BRIGHT}{Back.RED}Incorrect choice!')
except KeyboardInterrupt:
    print(f'\n{Style.BRIGHT}{Fore.YELLOW}Exiting...')
