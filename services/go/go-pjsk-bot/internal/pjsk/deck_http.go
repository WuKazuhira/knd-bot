package pjsk

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/kazuhira/go-pjsk-bot/internal/settings"
)

type deckHTTP struct {
	urls    []string
	api     string
	timeout time.Duration
	client  *http.Client
	cursor  atomic.Uint64
}

func newDeckHTTP(cfg settings.Deck) *deckHTTP {
	var urls []string
	for _, url := range cfg.ServiceURLs {
		if url = strings.TrimRight(strings.TrimSpace(url), "/"); url != "" {
			urls = append(urls, url)
		}
	}
	return &deckHTTP{
		urls: urls, api: cfg.ServiceAPI,
		timeout: time.Duration(cfg.HTTPTimeoutSeconds * float64(time.Second)),
		client:  &http.Client{},
	}
}

func (h *deckHTTP) orderedURLs() []string {
	if len(h.urls) == 0 {
		return nil
	}
	start := int((h.cursor.Add(1) - 1) % uint64(len(h.urls)))
	return append(slices.Clone(h.urls[start:]), h.urls[:start]...)
}

type deckHTTPError struct {
	status  int
	code    string
	message string
}

func (e *deckHTTPError) Error() string {
	if e.code == "" {
		return fmt.Sprintf("HTTP %d: %s", e.status, e.message)
	}
	return fmt.Sprintf("HTTP %d [%s]: %s", e.status, e.code, e.message)
}

func (h *deckHTTP) post(ctx context.Context, url string, timeout time.Duration, body []byte) (any, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	resp, err := h.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (8<<20)+1))
	if err != nil {
		return nil, err
	}
	var value any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	decodeErr := decoder.Decode(&value)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		obj, _ := value.(map[string]any)
		detail, _ := obj["error"].(map[string]any)
		message := strField(detail, "message")
		if message == "" {
			message = strField(obj, "detail")
		}
		if message == "" {
			message = strings.TrimSpace(string(raw[:min(len(raw), 500)]))
		}
		if message == "" {
			message = resp.Status
		}
		return nil, &deckHTTPError{resp.StatusCode, strField(detail, "code"), message}
	}
	if len(raw) > 8<<20 {
		return nil, errors.New("组卡响应过大")
	}
	if decodeErr != nil {
		return nil, fmt.Errorf("解析组卡响应: %w", decodeErr)
	}
	return value, nil
}

func canUseLegacy(err error) bool {
	var response *deckHTTPError
	if !errors.As(err, &response) || response.code == "unknown_region" || response.code == "invalid_request" {
		return false
	}
	switch response.status {
	case 404, 405, 501:
		return true
	}
	return false
}

func (h *deckHTTP) recommend(ctx context.Context, region string, options map[string]any, user []byte, challengeAll bool) ([]map[string]any, error) {
	urls := h.orderedURLs()
	if len(urls) == 0 {
		return nil, errors.New("未配置可用的 Allium HTTP 组卡服务")
	}
	params, err := translateDeckOptions(options)
	if err != nil {
		return nil, err
	}
	path := "/v1/recommend"
	if challengeAll {
		path += "/challenge-all"
		delete(params, "challengeLiveCharacterId")
		params["limit"] = 1
		if params["liveType"] == nil {
			params["liveType"] = "challenge"
		}
		if params["musicId"] == nil {
			params["musicId"] = 104
		}
		if params["musicDiff"] == nil {
			params["musicDiff"] = "master"
		}
	}
	v1, err := json.Marshal(map[string]any{"region": region, "user": string(user), "params": params})
	if err != nil {
		return nil, err
	}
	var legacy []byte
	if !challengeAll && h.api != "v1" {
		payload := make(map[string]any, len(options)+2)
		for key, value := range options {
			payload[key] = value
		}
		payload["region"] = region
		payload["user_data_str"] = string(user)
		if payload["algorithm"] == nil {
			payload["algorithm"] = "dfs"
		}
		if payload["timeout_ms"] == nil {
			payload["timeout_ms"] = 15000
		}
		legacy, err = json.Marshal(payload)
		if err != nil {
			return nil, err
		}
	}

	timeout := time.Duration(intField(options, "timeout_ms"))*time.Millisecond + 5*time.Second
	if h.timeout > timeout {
		timeout = h.timeout
	}
	var failures []string
	for _, url := range urls {
		var response any
		if h.api != "legacy" || challengeAll {
			response, err = h.post(ctx, url+path, timeout, v1)
		}
		if !challengeAll && (h.api == "legacy" || h.api == "auto" && canUseLegacy(err)) {
			response, err = h.post(ctx, url+"/recommend", timeout, legacy)
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if err == nil {
			if challengeAll {
				root, _ := response.(map[string]any)
				characters := sliceOfMap(root["characters"])
				decks := make([]any, 0, len(characters))
				for _, character := range characters {
					if deck, ok := character["deck"].(map[string]any); ok {
						if rank := character["rank"]; rank != nil {
							deck["rank"] = rank
						}
						decks = append(decks, deck)
					}
				}
				response = decks
			}
			if decks := normalizeDecks(response); len(decks) > 0 {
				return decks, nil
			}
			err = errors.New("组卡服务返回空卡组")
		}
		failures = append(failures, fmt.Sprintf("%s: %v", url, err))
	}
	return nil, errors.New(strings.Join(failures, "；"))
}

func (h *deckHTTP) probe(ctx context.Context, url string) string {
	var failures []string
	for _, path := range []string{"/readyz", "/healthz"} {
		probeCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		req, err := http.NewRequestWithContext(probeCtx, http.MethodGet, url+path, nil)
		if err == nil {
			var resp *http.Response
			resp, err = h.client.Do(req)
			if err == nil {
				resp.Body.Close()
				if resp.StatusCode >= 200 && resp.StatusCode < 300 {
					cancel()
					return "可用（" + path + "）"
				}
				err = fmt.Errorf("HTTP %d", resp.StatusCode)
			}
		}
		cancel()
		failures = append(failures, path+" "+err.Error())
	}
	return "不可用（" + strings.Join(failures, "，") + "）"
}

var deckOptionAliases = map[string]string{
	"live_type":                            "liveType",
	"event_id":                             "eventId",
	"event_type":                           "eventType",
	"event_unit":                           "eventUnit",
	"event_attr":                           "eventAttr",
	"custom_bonus_character_ids":           "customBonusCharacterIds",
	"custom_bonus_attr":                    "customBonusAttr",
	"custom_bonus_support_units":           "customBonusCharacterSupportUnits",
	"custom_bonus_character_support_units": "customBonusCharacterSupportUnits",
	"target_bonus_list":                    "targetBonusList",
	"world_bloom_character_id":             "worldBloomCharacterId",
	"world_bloom_chapter_no":               "worldBloomEventTurn",
	"world_bloom_event_turn":               "worldBloomEventTurn",
	"world_bloom_finale_turn":              "worldBloomFinaleTurn",
	"forced_leader_character_id":           "forcedLeaderCharacterId",
	"support_master_max":                   "supportMasterMax",
	"support_skill_max":                    "supportSkillMax",
	"fixed_cards":                          "fixedCards",
	"fixed_characters":                     "fixedCharacters",
	"excluded_cards":                       "excludedCards",
	"challenge_live_character_id":          "challengeLiveCharacterId",
	"unit_filter":                          "unitFilter",
	"attr_filter":                          "attrFilter",
	"filter_other_unit":                    "filterOtherUnit",
	"music_id":                             "musicId",
	"music_diff":                           "musicDiff",
	"best_skill_as_leader":                 "bestSkillAsLeader",
	"skill_order_choose_strategy":          "skillOrderChooseStrategy",
	"specific_skill_order":                 "specificSkillOrder",
	"skill_reference_choose_strategy":      "skillReferenceChooseStrategy",
	"keep_after_training_state":            "keepAfterTrainingState",
	"multi_live_teammate_power":            "multiLiveTeammatePower",
	"multi_live_teammate_score_up":         "multiLiveTeammateScoreUp",
	"multi_live_score_up_lower_bound":      "multiLiveScoreUpLowerBound",
	"other_score":                          "otherScore",
	"single_card_configs":                  "singleCardConfigs",
	"rarity_1_config":                      "rarity1Config",
	"rarity_2_config":                      "rarity2Config",
	"rarity_3_config":                      "rarity3Config",
	"rarity_4_config":                      "rarity4Config",
	"rarity_birthday_config":               "rarityBirthdayConfig",
	"timeout_ms":                           "timeoutMs",
}

func translateDeckOptions(options map[string]any) (map[string]any, error) {
	if member := options["member"]; member != nil {
		_, isString := member.(string)
		if isString || deckNumber(member) != 5 {
			return nil, fmt.Errorf("Allium HTTP 仅支持 5 人组卡，当前 member=%v", member)
		}
	}
	out := make(map[string]any, len(options))
	for key, value := range options {
		if value == nil {
			continue
		}
		switch key {
		case "algorithm", "region", "user_data_str", "member":
			continue
		case "world_bloom_chapter_no":
			// 真实活动由服务端按 eventId 推算回合。
			if options["event_id"] != nil || options["world_bloom_event_turn"] != nil {
				continue
			}
		}
		if alias := deckOptionAliases[key]; alias != "" {
			if options[alias] != nil || key == "custom_bonus_support_units" && options["custom_bonus_character_support_units"] != nil {
				continue
			}
			key = alias
		}
		out[key] = value
	}
	return out, nil
}

func deckValue(row map[string]any, keys ...string) any {
	for _, key := range keys {
		if value := row[key]; value != nil {
			return value
		}
	}
	return nil
}

func deckNumber(value any) float64 {
	var n float64
	switch v := value.(type) {
	case json.Number:
		n, _ = v.Float64()
	case float64:
		n = v
	case int:
		n = float64(v)
	case int64:
		n = float64(v)
	case string:
		n, _ = strconv.ParseFloat(strings.TrimSpace(v), 64)
	}
	if math.IsNaN(n) || math.IsInf(n, 0) {
		return 0
	}
	return n
}

func normalizeDecks(response any) []map[string]any {
	var raw []any
	switch data := response.(type) {
	case map[string]any:
		if result, ok := data["result"].(map[string]any); ok {
			data = result
		}
		raw, _ = data["decks"].([]any)
	case []any:
		for _, item := range data {
			row, _ := item.(map[string]any)
			if result, ok := row["result"].(map[string]any); ok {
				decks, _ := result["decks"].([]any)
				raw = append(raw, decks...)
			} else {
				raw = append(raw, item)
			}
		}
	}
	out := make([]map[string]any, 0, len(raw))
	for index, value := range raw {
		row, _ := value.(map[string]any)
		var cards []map[string]any
		for _, card := range sliceOfMap(row["cards"]) {
			cards = append(cards, normalizeDeckCard(card))
		}
		if len(cards) == 0 {
			continue
		}
		target := int64(deckNumber(deckValue(row, "target_value", "targetValue", "score")))
		eventPoint := deckValue(row, "event_point", "eventPoint")
		liveScore := deckValue(row, "live_score", "liveScore")
		score := target
		if liveScore != nil {
			score = int64(deckNumber(liveScore))
		}
		if eventPoint != nil {
			score = int64(deckNumber(eventPoint))
		}
		rank := index + 1
		if row["rank"] != nil {
			rank = int(deckNumber(row["rank"]))
		}
		out = append(out, map[string]any{
			"rank": rank, "target_value": target, "score": score, "cards": cards,
			"total_power":         int64(deckNumber(deckValue(row, "total_power", "totalPower"))),
			"event_bonus_rate":    deckNumber(deckValue(row, "event_bonus_rate", "eventBonusTotal", "eventBonus")),
			"multi_live_score_up": deckNumber(deckValue(row, "multi_live_score_up", "multiLiveScoreUp")),
			"live_score":          int64(deckNumber(liveScore)), "event_point": int64(deckNumber(eventPoint)),
		})
	}
	return out
}

func normalizeDeckCard(raw map[string]any) map[string]any {
	card := map[string]any{
		"card_id":        int(deckNumber(deckValue(raw, "card_id", "cardId", "id"))),
		"level":          int(deckNumber(raw["level"])),
		"skill_level":    int(deckNumber(deckValue(raw, "skill_level", "skillLevel"))),
		"skill_score_up": deckNumber(deckValue(raw, "skill_score_up", "skillScoreUp")),
	}
	for _, field := range []struct{ name, alias string }{
		{"power_total", "powerTotal"}, {"event_bonus", "eventBonus"}, {"master_rank", "masterRank"},
		{"special_training_status", "specialTrainingStatus"}, {"default_image", "defaultImage"},
		{"after_training", "afterTraining"}, {"trained", "trained"}, {"episodes_read", "episodesRead"},
		{"episode1_read", "episode1Read"}, {"episode2_read", "episode2Read"},
		{"has_canvas_bonus", "hasCanvasBonus"}, {"canvas_power", "canvasPower"}, {"is_virtual", "isVirtual"},
	} {
		if value := deckValue(raw, field.name, field.alias); value != nil {
			switch field.name {
			case "power_total", "master_rank", "canvas_power":
				value = int(deckNumber(value))
			case "event_bonus":
				value = deckNumber(value)
			}
			card[field.name] = value
		}
	}
	if trained, ok := card["trained"].(bool); ok && card["default_image"] == nil {
		card["default_image"] = "original"
		if trained {
			card["default_image"] = "special_training"
		}
	}
	if status, ok := card["special_training_status"].(string); ok && card["after_training"] == nil {
		switch strings.ToLower(strings.TrimSpace(status)) {
		case "done", "special_training", "trained", "after_training":
			card["after_training"] = true
		default:
			card["after_training"] = false
		}
	}
	if episodes, ok := card["episodes_read"].([]any); ok {
		for i, key := range []string{"episode1_read", "episode2_read"} {
			if card[key] == nil {
				card[key] = slices.ContainsFunc(episodes, func(episode any) bool {
					return deckNumber(episode) == float64(i+1)
				})
			}
		}
	}
	return card
}
