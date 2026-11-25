package mysekaidata

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/gameapi"
	"github.com/kazuhira/go-pjsk-bot/internal/serverconfig"
)

// Suite API 传未知字段会直接 404，别随便往里加 key
var suiteAPIKeys = []string{
	"userCards", "userDecks", "userGamedata", "userMusics", "userMusicResults",
	"userMysekaiMaterials", "userAreas", "userChallengeLiveSoloDecks", "userCharacters",
	"userMysekaiCanvases", "userMysekaiFixtureGameCharacterPerformanceBonuses",
	"userMysekaiGates", "userWorldBloomSupportDecks", "userHonors",
	"userMysekaiCharacterTalks", "userChallengeLiveSoloResults", "userChallengeLiveSoloStages",
	"userChallengeLiveSoloHighScoreRewards", "userEvents", "userWorldBlooms",
	"userMusicAchievements", "userPlayerFrames", "userMaterials", "upload_time",
	"userCharacterMissionV2s", "userCharacterMissionV2Statuses", "userBonds", "userProfileHonors",
}

type Fetcher struct {
	api      *gameapi.Client
	server   *serverconfig.Config
	cacheDir string
}

func NewFetcher(api *gameapi.Client, server *serverconfig.Config, dataDir string) *Fetcher {
	return &Fetcher{
		api:      api,
		server:   server,
		cacheDir: filepath.Join(dataDir, "ondemand", "profile", "mysekai"),
	}
}

func (f *Fetcher) SupportsUploadTime(serverType int) bool {
	return f.server != nil && f.server.MysekaiUploadTimeURL(serverType) != ""
}

func serverName(serverType int) string {
	switch serverType {
	case 1:
		return "tw"
	case 2:
		return "cn"
	default:
		return "jp"
	}
}

func (f *Fetcher) cacheFile(uid string, serverType int) string {
	return filepath.Join(f.cacheDir, serverName(serverType), uid+".json")
}

func (f *Fetcher) GetMysekaiInfo(ctx context.Context, uid string, serverType int, mode string, useCache bool) (map[string]any, string, error) {
	base := f.server.MysekaiURL(serverType, uid)
	if base == "" {
		return nil, "", fmt.Errorf("暂不支持该服务器的 MySekai 查询")
	}
	if mode == "" {
		mode = "latest"
	}
	sep := "?"
	if strings.Contains(base, "?") {
		sep = "&"
	}
	url := base + sep + "mode=" + mode

	raw, err := f.api.Get(ctx, url)
	if err == nil {
		var data map[string]any
		if e := json.Unmarshal(raw, &data); e == nil && len(data) > 0 {
			f.writeCache(uid, serverType, raw)
			return data, "", nil
		}
		err = fmt.Errorf("接口没有返回有效 MySekai 数据")
	}

	if useCache {
		if cached, e := os.ReadFile(f.cacheFile(uid, serverType)); e == nil {
			var data map[string]any
			if json.Unmarshal(cached, &data) == nil && len(data) > 0 {
				return data, fmt.Sprintf("接口获取失败，已使用本地缓存：%v", err), nil
			}
		}
	}
	return nil, "", fmt.Errorf("获取 MySekai 数据失败：%v", err)
}

func (f *Fetcher) writeCache(uid string, serverType int, raw []byte) {
	path := f.cacheFile(uid, serverType)
	if os.MkdirAll(filepath.Dir(path), 0o755) == nil {
		_ = os.WriteFile(path, raw, 0o644)
	}
}

func (f *Fetcher) GetSuiteData(ctx context.Context, uid string, serverType int) (map[string]any, string) {
	base := f.server.SuiteURL(serverType, uid)
	if base == "" {
		return nil, "此区服不支持 Suite 数据"
	}
	url := base + "?mode=latest&key=" + strings.Join(suiteAPIKeys, ",")
	raw, err := f.api.Get(ctx, url)
	if err != nil {
		return nil, fmt.Sprintf("Suite 数据获取失败：%v", err)
	}
	var data map[string]any
	if json.Unmarshal(raw, &data) != nil {
		return nil, "Suite 数据解析失败"
	}
	return data, ""
}

func (f *Fetcher) GetPhoto(ctx context.Context, uid string, serverType, seq int) ([]byte, int64, error) {
	info, _, err := f.GetMysekaiInfo(ctx, uid, serverType, "latest", true)
	if err != nil {
		return nil, 0, err
	}
	updated, _ := info["updatedResources"].(map[string]any)
	var photos []any
	if updated != nil {
		photos, _ = updated["userMysekaiPhotos"].([]any)
	}
	if len(photos) == 0 {
		return nil, 0, fmt.Errorf("没有查询到 MySekai 照片数据")
	}
	if seq == 0 {
		return nil, 0, fmt.Errorf("照片编号从 1 或 -1 开始")
	}
	if seq < 0 {
		seq = len(photos) + seq + 1
	}
	if seq < 1 || seq > len(photos) {
		return nil, 0, fmt.Errorf("照片编号超出范围，共 %d 张", len(photos))
	}
	photo := photos[seq-1]

	photoURL := f.server.MysekaiPhotoURL(serverType)
	if photoURL == "" || photoURL == "https://xxx" {
		return nil, 0, fmt.Errorf("当前未配置 MySekai 照片 API")
	}
	raw, err := f.api.PostJSON(ctx, photoURL, photo)
	if err != nil {
		return nil, 0, err
	}
	ts := int64(time.Now().Unix())
	if pm, ok := photo.(map[string]any); ok {
		if v := intOf(pm["obtainedAt"], 0); v != 0 {
			ts = int64(v)
		}
	}
	return raw, ts, nil
}

func ProfileFromSuiteData(uid string, data map[string]any) map[string]any {
	if data == nil {
		data = map[string]any{}
	}
	gamedata, _ := data["userGamedata"].(map[string]any)
	profileData := gamedata
	if profileData == nil {
		profileData = data
	}
	decks := sliceOf(firstNonNil(data["userDecks"], profileData["userDecks"]))
	cards := sliceOf(firstNonNil(data["userCards"], profileData["userCards"]))
	deckNum := intOf(profileData["deck"], 1)

	userDecks := make([]int64, 5)
	specialTraining := make([]bool, 5)
	for _, d := range decks {
		dm, ok := d.(map[string]any)
		if !ok || intOf(dm["deckId"], 0) != deckNum {
			continue
		}
		for i := 0; i < 5; i++ {
			cardID := intOf(dm[fmt.Sprintf("member%d", i+1)], 0)
			userDecks[i] = int64(cardID)
			for _, c := range cards {
				cm, ok := c.(map[string]any)
				if ok && intOf(cm["cardId"], 0) == cardID {
					specialTraining[i] = strOf(cm["defaultImage"]) == "special_training"
					break
				}
			}
		}
		break
	}

	return map[string]any{
		"userid":            uid,
		"name":              firstStr(strOf(data["name"]), strOf(profileData["name"]), "???"),
		"rank":              firstIntNonZero(intOf(data["rank"], 0), intOf(profileData["rank"], 0)),
		"userDecks":         userDecks,
		"special_training":  specialTraining,
		"userProfileHonors": firstNonNil(data["userProfileHonors"], profileData["userProfileHonors"], []any{}),
		"userHonorMissions": firstNonNil(data["userHonorMissions"], profileData["userHonorMissions"], []any{}),
		"suite_update_time": firstIntNonZero(intOf(data["upload_time"], 0), int(time.Now().Unix())),
	}
}
