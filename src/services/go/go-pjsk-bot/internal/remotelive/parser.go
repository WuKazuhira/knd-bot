package remotelive

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

const (
	eventRankKey    = "afterEventRanking"
	eventPointKey   = "afterEventPoint"
	worldChapterKey = "worldBloomChapterNo"
	worldRankKey    = "afterWorldBloomChapterRanking"
	worldPointKey   = "afterWorldBloomChapterPoint"
)

// LiveFields 是一次 remote live 响应中可落库的字段。
// EndStatus 用于区分 HTTP 200 但游戏服 live-end 未成功的情况。
type LiveFields struct {
	EndStatus      *int
	EventRank      *int
	EventPoint     *int
	WLChapterNo    *int
	WLChapterRank  *int
	WLChapterPoint *int
	Score          *int
	LiveID         *string
}

// HasEventResult 表示响应至少包含总榜排名或总榜分数。
func (f LiveFields) HasEventResult() bool {
	return f.EventRank != nil || f.EventPoint != nil
}

// ParseLiveResponse 递归解析 sekai-api live-end 响应。
// chapterNo 可选：当响应中存在多个 WL 分榜时优先选择该章节。
func ParseLiveResponse(raw []byte, chapterNo *int) (LiveFields, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var payload any
	if err := decoder.Decode(&payload); err != nil {
		return LiveFields{}, fmt.Errorf("解析 remote live 响应失败: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err == nil {
		return LiveFields{}, fmt.Errorf("解析 remote live 响应失败：存在多余 JSON 数据")
	}
	fields := LiveFields{}
	body := payload
	if root, ok := payload.(map[string]any); ok {
		fields.EndStatus = intPtrValue(root["endStatus"])
		fields.LiveID = stringPtrValue(root["userLiveId"])
		if endResponse, exists := root["endResponse"]; exists {
			body = endResponse
		}
	}
	fields.EventRank = findInt(body, eventRankKey)
	fields.EventPoint = findInt(body, eventPointKey)
	fields.Score = findInt(body, "score")

	entries := findWorldEntries(body)
	if len(entries) > 0 {
		chosen := entries[0]
		if chapterNo != nil {
			for _, entry := range entries {
				if entry.chapterNo != nil && *entry.chapterNo == *chapterNo {
					chosen = entry
					break
				}
			}
		}
		if chosen.rank == nil {
			for _, entry := range entries {
				if entry.rank != nil {
					chosen = entry
					break
				}
			}
		}
		fields.WLChapterNo = chosen.chapterNo
		fields.WLChapterRank = chosen.rank
		fields.WLChapterPoint = chosen.point
	}
	return fields, nil
}

type worldEntry struct {
	chapterNo *int
	rank      *int
	point     *int
}

func findWorldEntries(payload any) []worldEntry {
	var out []worldEntry
	forEachNode(payload, func(node map[string]any) {
		if _, hasRank := node[worldRankKey]; !hasRank {
			if _, hasPoint := node[worldPointKey]; !hasPoint {
				return
			}
		}
		rank := intPtrValue(node[worldRankKey])
		if rank == nil {
			rank = findInt(node[worldRankKey], worldRankKey)
		}
		point := intPtrValue(node[worldPointKey])
		if point == nil {
			point = findInt(node[worldPointKey], worldPointKey)
		}
		out = append(out, worldEntry{
			chapterNo: intPtrValue(node[worldChapterKey]),
			rank:      rank,
			point:     point,
		})
	})
	return out
}

func findInt(payload any, key string) *int {
	var result *int
	forEachNode(payload, func(node map[string]any) {
		if result != nil {
			return
		}
		raw, exists := node[key]
		if !exists {
			return
		}
		if value := intPtrValue(raw); value != nil {
			result = value
			return
		}
		if nested, ok := raw.(map[string]any); ok {
			for _, nestedKey := range []string{"rank", "ranking", "value", "point", "score"} {
				if value := intPtrValue(nested[nestedKey]); value != nil {
					result = value
					return
				}
			}
		}
	})
	return result
}

func forEachNode(value any, visit func(map[string]any)) {
	switch node := value.(type) {
	case map[string]any:
		visit(node)
		for _, child := range node {
			forEachNode(child, visit)
		}
	case []any:
		for _, child := range node {
			forEachNode(child, visit)
		}
	}
}

func intPtrValue(value any) *int {
	var text string
	switch v := value.(type) {
	case json.Number:
		text = v.String()
	case string:
		text = strings.TrimSpace(v)
	case float64:
		if v != float64(int(v)) {
			return nil
		}
		result := int(v)
		return &result
	case int:
		result := v
		return &result
	case int64:
		result := int(v)
		return &result
	default:
		return nil
	}
	if text == "" {
		return nil
	}
	result, err := strconv.Atoi(text)
	if err != nil {
		return nil
	}
	return &result
}

func stringPtrValue(value any) *string {
	text, ok := value.(string)
	if !ok || strings.TrimSpace(text) == "" {
		return nil
	}
	text = strings.TrimSpace(text)
	return &text
}
