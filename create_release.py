import json
import requests
import os
import tempfile
import tarfile
import hashlib
from typing import Dict, Union, List

def get_released_versions(per_page=30):
    """
    Retrieves the released versions of the Arduino Boards from the GitHub API.

    Args:
        per_page (int): The number of releases to retrieve per page. Defaults to 30.

    Returns:
        dict: A dictionary containing the released versions of the Arduino Boards, indexed by platform.
    """
    released_versions = {}
    page_number = 0
    while True:
        releases = requests.get('https://api.github.com/repos/AaronLi/Arduino-Boards/releases', headers={"accept": "application/vnd.github+json", 'per_page':str(per_page), 'page':str(page_number), "X-Github-Api-Version": '2022-11-28'}).json()
        for release in releases:
            for asset in release['assets']:
                version, hash_platform_file = asset['name'].split('_', 1)
                hash, platform_file = hash_platform_file.split('_', 1)
                platform, extension = platform_file.split('.', 1)
                print(f"Version {version} for {platform} filetype {extension} with sha256 {hash}")
                released_versions[platform] = list(map(int, version.split('.')))

        if len(releases) < per_page:
            break
        page_number += 1
    return released_versions

def get_commited_versions():
    """
    Returns a dictionary containing the version numbers of each board in the 'boards' directory that has a 'platform.txt' file.
    The dictionary keys are the board names and the values are lists of integers representing the version numbers.
    """
    commit_versions = {}
    for board in os.listdir('boards'):
        config_file = os.path.join('boards', board, 'platform.txt')
        if not os.path.exists(config_file):
            continue
        
        with open(config_file) as f:
            for line in f:
                if line.startswith('version'):
                    version = line.split('=', 1)[1].strip()
                    commit_versions[board] = list(map(int, version.split('.')))
                    break
    return commit_versions

def get_updated_boards(released_versions, commited_versions):
    """
    Returns a dictionary of boards and their updated versions based on the input of released and committed versions.

    Args:
    released_versions (dict): A dictionary of boards and their released versions.
    commited_versions (dict): A dictionary of boards and their committed versions.

    Returns:
    dict: A dictionary of boards and their updated versions.
    Returned values are boards that have been updated.
    """
    updated = {}
    for board, version in commited_versions.items():
        if board not in released_versions:
            print("New board: ", board)
            updated[board] = version
        else:
            for commit_sub_version, release_sub_version in zip(version, released_versions[board]):
                if commit_sub_version > release_sub_version:
                    updated[board] = version
                    break
                elif commit_sub_version < release_sub_version:
                    raise ValueError("Board {} has older version than release: {} < {}".format(board, version, released_versions[board]))
    return updated

def create_board_archives(work_dir, updated_boards):
    """
    Create compressed archives for updated boards.

    Args:
        work_dir (str): The path to the working directory.
        updated_boards (dict): A dictionary containing the names of the updated boards as keys and their new version numbers as values.

    Returns:
        dict: A dictionary containing the paths to the newly created archives, their version numbers, and the names of the boards they correspond to.
    """
    new_archives = {}
    for board, version in updated_boards.items():
        version_str = '.'.join(map(str, version))
        print(f"Board {board} has new version {version_str}")

        compressed_file_path = os.path.join(work_dir, f"{board}.tar.bz2")
        with tarfile.open(compressed_file_path, "w:bz2") as tar:
            
            tar.add(os.path.join('boards', board), arcname=board)

        with open(compressed_file_path, 'rb') as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()
        
        # rename file to {version}_{sha256}_{platform}.tar.bz2
        final_file_path = f"{version_str}_{sha256}_{board}.tar.bz2"
        new_file_path = os.path.join(tmpdir, final_file_path)
        os.rename(compressed_file_path, new_file_path)

        new_archives[board] = {"local_path": new_file_path, "version": version_str, "filename": final_file_path}
    return new_archives

def create_tag_name(updated_boards: Dict[str, Dict[str, Union[str, int]]]):
    """
    Creates a tag name for a release based on the updated boards.

    Args:
        updated_boards (Dict[str, Dict[str, Union[str, int]]]): A dictionary containing the updated boards.

    Returns:
        str: A string representing the tag name for the release.
    """
    
    # take 4 letters from the end of each board name and the version number and join them with underscores
    tag_name = '_'.join([f"{board[-4:]}_{info['version']}" for board, info in updated_boards.items()])
    return tag_name

def create_release_title(updated_boards: Dict[str, Dict[str, Union[str, int]]]):
    return "New Board Definition Versions Released"

def create_release_body(updated_boards: Dict[str, Dict[str, Union[str, int]]], released_versions: Dict[str, List[int]]):
    version_change_strings = []
    for n, board_info in enumerate(sorted(updated_boards.items(), key=lambda x: x[1]['version'])):
        board, info = board_info
        version_change_strings.append(f"1. `{board:24} {'.'.join(released_versions.get(board)) if board in released_versions else 'new'} -> {info['version']}`")
                                      
    return "New versions have been released for the following boards:\n" + '\n'.join(version_change_strings)

def create_release(auth_token: str, updated_boards: Dict[str, Dict[str, Union[str, int]]], released_boards: Dict[str, List[int]]):
    release_body = {
                "tag_name": create_tag_name(updated_boards),
                "draft": True,
                "name": create_release_title(updated_boards),
                "body": create_release_body(updated_boards, released_versions=released_boards),
            }

    response = requests.post(
        'https://api.github.com/repos/AaronLi/Arduino-Boards/releases',
          headers={
              "accept": "application/vnd.github+json",
               'Authorization': f'Bearer {auth_token}',
               "X-Github-Api-Version": '2022-11-28'
            },
            data=json.dumps(release_body))
    
    return response.json()['id']

def upload_assets(auth_token: str, release_id: int, updated_boards: Dict[str, Dict[str, Union[str, int]]]):
    for board, info in updated_boards.items():
        with open(info['local_path'], 'rb') as f:
            response = requests.post(
                f'https://uploads.github.com/repos/AaronLi/Arduino-Boards/releases/{release_id}/assets?name={updated_boards[board]["filename"]}',
                headers={
                    "accept": "application/vnd.github+json",
                    'Authorization': f'Bearer {auth_token}',
                    "X-Github-Api-Version": '2022-11-28',
                    'Content-Type': 'application/octet-stream'
                },
                data=f.read())
            print(response.json()['name'], response.json()['state'])

released_versions = get_released_versions()
print('Versions in release:', released_versions)

commit_versions = get_commited_versions()
print('Versions in commit:', commit_versions)

updated = get_updated_boards(released_versions, commit_versions)
print('Updated versions:', updated)

if not updated:
    print("No updates found.")
    exit()

with tempfile.TemporaryDirectory() as tmpdir:
    
    new_archives = create_board_archives(tmpdir, updated)

    print(new_archives)

    auth_token = os.environ['GITHUB_TOKEN']
    release_id = create_release(auth_token, new_archives, released_versions)
    upload_assets(auth_token, release_id, new_archives)