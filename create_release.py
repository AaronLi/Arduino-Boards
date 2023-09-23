import json
import asyncio
import aiohttp
import aiofiles
import aiofiles.os
import aiofiles.tempfile
import os.path as path
import os
import tarfile
import hashlib
from typing import Dict, Union, List

def version_ordering(a: List[int], b: List[int]):
    """
    Compares two version numbers represented as lists of integers.

    Args:
        a (list): The first version number.
        b (list): The second version number.

    Returns:
        int: -1 if a < b, 0 if a == b, 1 if a > b.
    """
    for a_sub_version, b_sub_version in zip(a, b):
        if a_sub_version < b_sub_version:
            return -1
        elif a_sub_version > b_sub_version:
            return 1
    return 0

async def get_released_versions(session: aiohttp.ClientSession, per_page=30):
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
        async with session.get('https://api.github.com/repos/AaronLi/Arduino-Boards/releases', headers={'accept': 'application/vnd.github+json', 'per_page': str(per_page), 'page':str(page_number), "X-Github-Api-Version": '2022-11-28'}) as req:
            releases = await req.json()
            for release in releases:
                for asset in release['assets']:
                    if asset['name'].startswith('manifest'):
                        continue
                    version, hash_platform_file = asset['name'].split('_', 1)
                    hash, platform_file = hash_platform_file.split('_', 1)
                    platform, extension = platform_file.split('.', 1)
                    print(f"Version {version} for {platform} filetype {extension} with sha256 {hash}")
                    version = list(map(int, version.split('.')))
                    if platform not in released_versions or version_ordering(version, released_versions[platform]) > 0:
                        released_versions[platform] = version

            if len(releases) < per_page:
                break
            page_number += 1
    return released_versions

async def get_commited_versions():
    """
    Returns a dictionary containing the version numbers of each board in the 'platforms' directory that has a 'platform.txt' file.
    The dictionary keys are the board names and the values are lists of integers representing the version numbers.
    """
    commit_versions = {}

    for platform in await aiofiles.os.listdir('platforms'):
        config_file = path.join('platforms', platform, 'platform.txt')
        if not await aiofiles.os.path.exists(config_file):
            continue
    
        async with aiofiles.open(config_file) as f:
            async for line in f:
                if line.startswith('version'):
                    version = line.split('=', 1)[1].strip()
                    commit_versions[platform] = list(map(int, version.split('.')))
                    break
    return commit_versions

def get_updated_platforms(released_versions, commited_versions):
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
    for platform, version in commited_versions.items():
        if platform not in released_versions:
            print("New board: ", platform)
            updated[platform] = version
        else:
            for commit_sub_version, release_sub_version in zip(version, released_versions[platform]):
                if commit_sub_version > release_sub_version:
                    updated[platform] = version
                    break
                elif commit_sub_version < release_sub_version:
                    raise ValueError("Board {} has older version than release: {} < {}".format(platform, version, released_versions[platform]))
    return updated

async def create_platform_archives(work_dir, updated_platforms):
    """
    Create compressed archives for updated boards.

    Args:
        work_dir (str): The path to the working directory.
        updated_boards (dict): A dictionary containing the names of the updated boards as keys and their new version numbers as values.

    Returns:
        dict: A dictionary containing the paths to the newly created archives, their version numbers, and the names of the boards they correspond to.
    """
    new_archives = {}
    for platform, version in updated_platforms.items():
        version_str = '.'.join(map(str, version))
        print(f"Board {platform} has new version {version_str}")

        compressed_file_path = path.join(work_dir, f"{platform}.tar.bz2")
        with tarfile.open(compressed_file_path, "w:bz2") as tar:
            
            tar.add(path.join('platforms', platform), arcname=platform)

        async with aiofiles.open(compressed_file_path, 'rb') as f:
            sha256 = hashlib.sha256(await f.read()).hexdigest()
        
        # rename file to {version}_{sha256}_{platform}.tar.bz2
        final_file_path = f"{version_str}_{sha256}_{platform}.tar.bz2"
        new_file_path = path.join(work_dir, final_file_path)
        await aiofiles.os.rename(compressed_file_path, new_file_path)

        new_archives[platform] = {"local_path": new_file_path, "version": version_str, "filename": final_file_path}
    return new_archives

def create_tag_name(updated_platforms: Dict[str, Dict[str, Union[str, int]]]):
    """
    Creates a tag name for a release based on the updated boards.

    Args:
        updated_boards (Dict[str, Dict[str, Union[str, int]]]): A dictionary containing the updated boards.

    Returns:
        str: A string representing the tag name for the release.
    """
    
    # take 4 letters from the end of each board name and the version number and join them with underscores
    tag_name = '_'.join([f"{board[-4:]}_{info['version']}" for board, info in updated_platforms.items()])
    return tag_name

def create_release_title(updated_platforms: Dict[str, Dict[str, Union[str, int]]]):
    return "New Board Definition Versions Released"

def create_release_body(updated_platforms: Dict[str, Dict[str, Union[str, int]]], released_versions: Dict[str, List[int]]):
    version_change_strings = []
    for n, board_info in enumerate(sorted(updated_platforms.items(), key=lambda x: x[1]['version'])):
        board, info = board_info
        version_change_strings.append(f"1. `{board:24} {'.'.join(released_versions.get(board)) if board in released_versions else 'new'} -> {info['version']}`")
                                      
    return "New versions have been released for the following boards:\n" + '\n'.join(version_change_strings)

async def create_release(session: aiohttp.ClientSession, auth_token: str, updated_platforms: Dict[str, Dict[str, Union[str, int]]], released_platforms: Dict[str, List[int]]):
    release_body = {
                "tag_name": create_tag_name(updated_platforms),
                "draft": True,
                "name": create_release_title(updated_platforms),
                "body": create_release_body(updated_platforms, released_versions=released_platforms),
            }
    async with session.post(
        'https://api.github.com/repos/AaronLi/Arduino-Boards/releases',
          headers={
              "accept": "application/vnd.github+json",
               'Authorization': f'Bearer {auth_token}',
               "X-Github-Api-Version": '2022-11-28'
            },
            data=json.dumps(release_body)) as req:
        json_response = await req.json()
        print(json_response)
        return json_response['id']

async def upload_assets(session: aiohttp.ClientSession, auth_token: str, release_id: str, updated_platforms: Dict[str, Dict[str, Union[str, int]]]):
    for board, info in updated_platforms.items():
        async with aiofiles.open(info['local_path'], 'rb') as f:
            async with session.post(
                f'https://uploads.github.com/repos/AaronLi/Arduino-Boards/releases/{release_id}/assets?name={updated_platforms[board]["filename"]}',
                headers={
                    "accept": "application/vnd.github+json",
                    'Authorization': f'Bearer {auth_token}',
                    "X-Github-Api-Version": '2022-11-28',
                    'Content-Type': 'application/octet-stream'
                },
                data=await f.read()) as req:
                response_json = await req.json()
                print(response_json['name'], response_json['state'])

async def upload_manifest(session: aiohttp.ClientSession, auth_token: str, release_id: str, released_platforms, updated_platforms):
    combined_platforms = {**released_platforms, **updated_platforms}
    manifest = {}
    for platform in combined_platforms:
        platform_directory = path.join('platforms', platform)
        platform_boards = path.join(platform_directory, 'boards.txt')
        variants_directory = path.join(platform_directory, 'variants')
        boards = set(path.basename(path.normpath(board)) for board in await aiofiles.os.listdir(variants_directory))
        board_names = []
        async with aiofiles.open(platform_boards) as f:
            async for line in f:
                if 'name' in line:
                    board_id, line_name = line.split('=', 1)
                    board_name = line_name.strip()
                    if board_id.split('.')[0] in boards:
                        board_names.append(board_name)

        async with aiofiles.open(path.join(platform_directory, 'architecture.txt')) as f:
            architecture = (await f.read()).strip()
        manifest[platform] = {
            'boards': board_names,
            'architecture': architecture,
            'version': '.'.join(map(str, combined_platforms[platform]))
        }
    async with session.post(
                f'https://uploads.github.com/repos/AaronLi/Arduino-Boards/releases/{release_id}/assets?name=manifest.json',
                headers={
                    "accept": "application/vnd.github+json",
                    'Authorization': f'Bearer {auth_token}',
                    "X-Github-Api-Version": '2022-11-28',
                    'Content-Type': 'application/octet-stream'
                },
                data=json.dumps(manifest)) as req:
        response = await req.json()
        print(response['name'], response['state'])


async def main():
    async with aiohttp.ClientSession() as session:
        released_versions, commit_versions = await asyncio.gather(get_released_versions(session), get_commited_versions())
        print('Versions in release:', released_versions)
        print('Versions in commit:', commit_versions)

        updated = get_updated_platforms(released_versions, commit_versions)
        print('Updated versions:', updated)

        if not updated:
            print("No updates found.")
            exit()

        async with aiofiles.tempfile.TemporaryDirectory() as tmpdir:
            
            new_archives = await create_platform_archives(tmpdir, updated)

            print(new_archives)

            auth_token = os.environ['GH_API_TOKEN']
            release_id = await create_release(session, auth_token, new_archives, released_versions)
            await asyncio.gather(
                upload_assets(session, auth_token, release_id, new_archives),
                upload_manifest(session, auth_token, release_id, released_versions, updated)
                )
        
if __name__ == "__main__":
    asyncio.run(main())