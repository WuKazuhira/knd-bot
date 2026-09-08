"""牌子（honor）绘制。

原 plugins/pjsk/_utils.py 的 generatehonor / bondsbackground。
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from utils.pjsk_paths import ONDEMAND_PATH, STATIC_PATH

from .context import get_context
from .primitives import get_pjsk_font, open_pjsk_image

data_path = ONDEMAND_PATH


# 牌子信息
async def generatehonor(honor, ismain=True, userHonorMissions=None, pjsk_type: int = 0):
    ctx = get_context()
    async_load_master_data = ctx.async_load_master_data
    get_asset = ctx.get_asset

    userHonorMissions = userHonorMissions if userHonorMissions else []
    pic = None
    star = False
    backgroundAssetbundleName = ''
    assetbundleName = ''
    honorRarity = 0
    honorType = ''
    honor['profileHonorType'] = honor.get('profileHonorType', 'normal')
    is_live_master = False

    if honor['profileHonorType'] == 'normal':
        # 普通牌子
        honors = await async_load_master_data('honors.json', pjsk_type)
        honorGroups = await async_load_master_data('honorGroups.json', pjsk_type)
        for i in honors:
            if i['id'] == honor['honorId']:
                try:
                    honorMissionType = ''
                    assetbundleName = i['assetbundleName']
                    honorRarity = i['honorRarity']
                    try:
                        star = True
                    except IndexError:
                        pass
                    for j in honorGroups:
                        if j['id'] == i['groupId']:
                            try:
                                backgroundAssetbundleName = j['backgroundAssetbundleName']
                            except KeyError:
                                backgroundAssetbundleName = ''
                            honorType = j['honorType']
                            break
                    filename = 'honor'
                    mainname = 'rank_main.png'
                    subname = 'rank_sub.png'
                except KeyError:
                    honorMissionType = i['honorMissionType']
                    for level in i['levels']:
                        if honor['honorLevel'] == level['level']:
                            assetbundleName = level['assetbundleName']
                            honorRarity = level['honorRarity']
                    filename = 'honor'
                    mainname = 'scroll.png'
                    subname = 'scroll.png'
                    is_live_master = True
                break
        else:
            raise AttributeError("找不到对应honor资源")
        if honorType == 'rank_match':
            filename = 'rank_live/honor'
            mainname = 'main.png'
            subname = 'sub.png'
        # 数据读取完成
        if ismain:
            # 大图
            if honorRarity == 'low':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_1.png')
            elif honorRarity == 'middle':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_2.png')
            elif honorRarity == 'high':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_3.png')
            else:
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_4.png')
            if backgroundAssetbundleName == '':
                rankpic = None
                pic = await get_asset(
                    rf'startapp/{filename}/{assetbundleName}', r'degree_main.png',
                    pjsk_type=pjsk_type
                )
                try:
                    rankpic = await get_asset(
                        f'startapp/{filename}/{assetbundleName}', mainname,
                        pjsk_type=pjsk_type
                    )
                except:
                    pass
                r, g, b, mask = frame.split()
                if honorRarity == 'low':
                    pic.paste(frame, (8, 0), mask)
                else:
                    pic.paste(frame, (0, 0), mask)
                if rankpic is not None:
                    r, g, b, mask = rankpic.split()
                    if is_live_master:
                        pic.paste(rankpic, (218, 3), mask)
                        for i in userHonorMissions:
                            if honorMissionType == i['honorMissionType']:
                                progress = i['progress']
                                break
                        else:
                            raise UnboundLocalError("未找到玩家对应的progress")
                        draw = ImageDraw.Draw(pic)
                        font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 20)
                        text_width = font_style.getsize(str(progress))
                        text_coordinate = (int(270 - text_width[0] / 2), int(58 - text_width[1] / 2))
                        draw.text(text_coordinate, str(progress), fill=(255, 255, 255), font=font_style)

                        star_count = (progress // 10) % 10 + 1
                        stars_pos = [
                            (223, 68), (216, 56), (208, 42), (216, 27), (223, 13),
                            (295, 68), (304, 56), (311, 42), (303, 27), (295, 13)
                        ]

                        with_star = open_pjsk_image(STATIC_PATH / 'pics/live_master_honor_star_1.png')
                        with_star_alpha = with_star.split()[3]
                        without_star = open_pjsk_image(STATIC_PATH / 'pics/live_master_honor_star_2.png')
                        without_star_alpha = without_star.split()[3]

                        for i in range(10):
                            if star_count <= i:
                                star_pic, star_alpha = without_star, without_star_alpha
                            else:
                                star_pic, star_alpha = with_star, with_star_alpha
                            pic.paste(star_pic, (stars_pos[i][0], stars_pos[i][1] - 8), star_alpha)
                    else:
                        rank_x = 0 if rankpic.width >= pic.width - 20 else 190
                        pic.paste(rankpic, (rank_x, 0), mask)
            else:
                pic = await get_asset(
                    rf'startapp/{filename}/{backgroundAssetbundleName}', r'degree_main.png',
                    pjsk_type=pjsk_type
                )
                rankpic = await get_asset(
                    rf'startapp/{filename}/{assetbundleName}', mainname,
                    pjsk_type=pjsk_type
                )
                r, g, b, mask = frame.split()
                if honorRarity == 'low':
                    pic.paste(frame, (8, 0), mask)
                else:
                    pic.paste(frame, (0, 0), mask)
                if rankpic is not None:
                    r, g, b, mask = rankpic.split()
                    rank_x = 0 if rankpic.width >= pic.width - 20 else 190
                    pic.paste(rankpic, (rank_x, 0), mask)
            if honorType == 'character' or honorType == 'achievement':
                honorlevel = honor['honorLevel']
                if star is True:
                    if honorlevel > 10:
                        honorlevel = honorlevel - 10
                    if honorlevel < 5:
                        for i in range(0, honorlevel):
                            lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv.png')
                            r, g, b, mask = lv.split()
                            pic.paste(lv, (54 + 16 * i, 63), mask)
                    else:
                        for i in range(0, 5):
                            lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv.png')
                            r, g, b, mask = lv.split()
                            pic.paste(lv, (54 + 16 * i, 63), mask)
                        for i in range(0, honorlevel - 5):
                            lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv6.png')
                            r, g, b, mask = lv.split()
                            pic.paste(lv, (54 + 16 * i, 63), mask)
        else:
            # 小图
            if honorRarity == 'low':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_1.png')
            elif honorRarity == 'middle':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_2.png')
            elif honorRarity == 'high':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_3.png')
            else:
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_4.png')
            if backgroundAssetbundleName == '':
                rankpic = None
                pic = await get_asset(
                    rf'startapp/{filename}/{assetbundleName}', r'degree_sub.png',
                    pjsk_type=pjsk_type
                )
                try:
                    # 小牌子的 rank_sub.png 不再调用
                    if subname != 'rank_sub.png':
                        rankpic = await get_asset(
                            f'startapp/{filename}/{assetbundleName}', subname,
                            pjsk_type=pjsk_type
                        )
                except:
                    pass
                r, g, b, mask = frame.split()
                if honorRarity == 'low':
                    pic.paste(frame, (8, 0), mask)
                else:
                    pic.paste(frame, (0, 0), mask)
                if rankpic is not None:
                    r, g, b, mask = rankpic.split()
                    if is_live_master:
                        pic.paste(rankpic, (40, 3), mask)
                        for i in userHonorMissions:
                            if honorMissionType == i['honorMissionType']:
                                progress = i['progress']
                                break
                        else:
                            raise UnboundLocalError("未找到玩家对应的progress")
                        draw = ImageDraw.Draw(pic)
                        font_style = get_pjsk_font("SourceHanSansCN-Bold.otf", 20)
                        text_width = font_style.getsize(str(progress))
                        text_coordinate = (int(90 - text_width[0] / 2), int(58 - text_width[1] / 2))
                        draw.text(text_coordinate, str(progress), fill=(255, 255, 255), font=font_style)
                    else:
                        pic.paste(rankpic, (34, 42), mask)
            else:
                pic = await get_asset(
                    rf'startapp/{filename}/{backgroundAssetbundleName}', r'degree_sub.png',
                    pjsk_type=pjsk_type
                )
                rankpic = None
                try:
                    if subname != 'rank_sub.png':
                        rankpic = await get_asset(
                            f'startapp/{filename}/{assetbundleName}', subname,
                            pjsk_type=pjsk_type
                        )
                    if rankpic is None:
                        rankpic = await get_asset(
                            f'startapp/{filename}/{assetbundleName}', 'rank_main.png',
                            pjsk_type=pjsk_type
                        )
                except Exception:
                    pass
                if pic is None:
                    return None
                r, g, b, mask = frame.split()
                if honorRarity == 'low':
                    pic.paste(frame, (8, 0), mask)
                else:
                    pic.paste(frame, (0, 0), mask)
                if rankpic is not None:
                    if rankpic.width >= pic.width - 20 or rankpic.height > pic.height:
                        target_size = (pic.width, min(38, pic.height))
                        rankpic = rankpic.resize(target_size, Image.Resampling.LANCZOS)
                        rank_y = max(0, (pic.height - rankpic.height) // 2)
                        pic.paste(rankpic, (0, rank_y), rankpic)
                    else:
                        r, g, b, mask = rankpic.split()
                        pic.paste(rankpic, (34, 42), mask)
            if honorType == 'character' or honorType == 'achievement':
                if star is True:
                    honorlevel = honor['honorLevel']
                    if honorlevel > 10:
                        honorlevel = honorlevel - 10
                    if honorlevel < 5:
                        for i in range(0, honorlevel):
                            lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv.png')
                            r, g, b, mask = lv.split()
                            pic.paste(lv, (54 + 16 * i, 63), mask)
                    else:
                        for i in range(0, 5):
                            lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv.png')
                            r, g, b, mask = lv.split()
                            pic.paste(lv, (54 + 16 * i, 63), mask)
                        for i in range(0, honorlevel - 5):
                            lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv6.png')
                            r, g, b, mask = lv.split()
                            pic.paste(lv, (54 + 16 * i, 63), mask)
    elif honor['profileHonorType'] == 'bonds':
        # cp牌子
        bondsHonors = await async_load_master_data('bondsHonors.json', pjsk_type)
        for i in bondsHonors:
            if i['id'] == honor['honorId']:
                gameCharacterUnitId1 = i['gameCharacterUnitId1']
                gameCharacterUnitId2 = i['gameCharacterUnitId2']
                honorRarity = i['honorRarity']
                break
        if ismain:
            # 大图
            if honor['bondsHonorViewType'] == 'reverse':
                pic = bondsbackground(gameCharacterUnitId2, gameCharacterUnitId1)
            else:
                pic = bondsbackground(gameCharacterUnitId1, gameCharacterUnitId2)
            chara1 = open_pjsk_image(data_path /
                                rf'chara/chr_sd_{str(gameCharacterUnitId1).zfill(2)}_01/chr_sd_'
                                rf'{str(gameCharacterUnitId1).zfill(2)}_01.png')
            chara2 = open_pjsk_image(data_path /
                                rf'chara/chr_sd_{str(gameCharacterUnitId2).zfill(2)}_01/chr_sd_'
                                rf'{str(gameCharacterUnitId2).zfill(2)}_01.png')
            if honor['bondsHonorViewType'] == 'reverse':
                chara1, chara2 = chara2, chara1
            r, g, b, mask = chara1.split()
            pic.paste(chara1, (0, -40), mask)
            r, g, b, mask = chara2.split()
            pic.paste(chara2, (220, -40), mask)
            if honorRarity == 'low':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_1.png')
            elif honorRarity == 'middle':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_2.png')
            elif honorRarity == 'high':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_3.png')
            else:
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_m_4.png')
            r, g, b, mask = frame.split()
            if honorRarity == 'low':
                pic.paste(frame, (8, 0), mask)
            else:
                pic.paste(frame, (0, 0), mask)
            wordbundlename = f"honorname_{str(gameCharacterUnitId1).zfill(2)}" \
                             f"{str(gameCharacterUnitId2).zfill(2)}_{str(honor['bondsHonorWordId']%100).zfill(2)}_01"
            word = None
            try:
                word = await get_asset(
                    r'startapp/bonds_honor/word', rf'{wordbundlename}.png',
                    pjsk_type=pjsk_type
                )
            except:
                pass
            if word is not None:
                r, g, b, mask = word.split()
                pic.paste(word, (int(190-(word.size[0]/2)), int(40-(word.size[1]/2))), mask)
            if honor['honorLevel'] < 5:
                for i in range(0, honor['honorLevel']):
                    lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv.png')
                    r, g, b, mask = lv.split()
                    pic.paste(lv, (54 + 16 * i, 63), mask)
            else:
                for i in range(0, 5):
                    lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv.png')
                    r, g, b, mask = lv.split()
                    pic.paste(lv, (54 + 16 * i, 63), mask)
                for i in range(0, honor['honorLevel'] - 5):
                    lv = open_pjsk_image(STATIC_PATH / 'pics/icon_degreeLv6.png')
                    r, g, b, mask = lv.split()
                    pic.paste(lv, (54 + 16 * i, 63), mask)
        else:
            # 小图
            if honor['bondsHonorViewType'] == 'reverse':
                pic = bondsbackground(gameCharacterUnitId2, gameCharacterUnitId1, False)
            else:
                pic = bondsbackground(gameCharacterUnitId1, gameCharacterUnitId2, False)
            chara1 = open_pjsk_image(data_path /
                                rf'chara/chr_sd_{str(gameCharacterUnitId1).zfill(2)}_01/chr_sd_'
                                rf'{str(gameCharacterUnitId1).zfill(2)}_01.png')
            chara2 = open_pjsk_image(data_path /
                                rf'chara/chr_sd_{str(gameCharacterUnitId2).zfill(2)}_01/chr_sd_'
                                rf'{str(gameCharacterUnitId2).zfill(2)}_01.png')
            if honor['bondsHonorViewType'] == 'reverse':
                chara1, chara2 = chara2, chara1
            chara1 = chara1.resize((120, 102))
            r, g, b, mask = chara1.split()
            pic.paste(chara1, (-5, -20), mask)
            chara2 = chara2.resize((120, 102))
            r, g, b, mask = chara2.split()
            pic.paste(chara2, (60, -20), mask)
            maskimg = open_pjsk_image(STATIC_PATH / 'pics/mask_degree_sub.png')
            r, g, b, mask = maskimg.split()
            pic.putalpha(mask)
            if honorRarity == 'low':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_1.png')
            elif honorRarity == 'middle':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_2.png')
            elif honorRarity == 'high':
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_3.png')
            else:
                frame = open_pjsk_image(STATIC_PATH / r'pics/frame_degree_s_4.png')
            r, g, b, mask = frame.split()
            if honorRarity == 'low':
                pic.paste(frame, (8, 0), mask)
            else:
                pic.paste(frame, (0, 0), mask)
            if honor['honorLevel'] < 5:
                for i in range(0, honor['honorLevel']):
                    lv = open_pjsk_image(STATIC_PATH / r'pics/icon_degreeLv.png')
                    r, g, b, mask = lv.split()
                    pic.paste(lv, (54 + 16 * i, 63), mask)
            else:
                for i in range(0, 5):
                    lv = open_pjsk_image(STATIC_PATH / r'pics/icon_degreeLv.png')
                    r, g, b, mask = lv.split()
                    pic.paste(lv, (54 + 16 * i, 63), mask)
                for i in range(0, honor['honorLevel'] - 5):
                    lv = open_pjsk_image(STATIC_PATH / r'pics/icon_degreeLv6.png')
                    r, g, b, mask = lv.split()
                    pic.paste(lv, (54 + 16 * i, 63), mask)
    return pic


# 牌子背景图
def bondsbackground(chara1, chara2, ismain=True):
    if ismain:
        pic1 = open_pjsk_image(STATIC_PATH / rf'bonds/{str(chara1)}.png')
        pic2 = open_pjsk_image(STATIC_PATH / rf'bonds/{str(chara2)}.png')
        pic2 = pic2.crop((190, 0, 380, 80))
        pic1.paste(pic2, (190, 0))
    else:
        pic1 = open_pjsk_image(STATIC_PATH / rf'bonds/{str(chara1)}_sub.png')
        pic2 = open_pjsk_image(STATIC_PATH / rf'bonds/{str(chara2)}_sub.png')
        pic2 = pic2.crop((90, 0, 380, 80))
        pic1.paste(pic2, (90, 0))
    return pic1
