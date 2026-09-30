package pjsk

import (
	"context"
	"encoding/base64"
	"fmt"
	"log"
	"strconv"
	"strings"

	"github.com/kazuhira/go-pjsk-bot/internal/cards"
	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/masterdata"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/profile"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
	"github.com/kazuhira/go-pjsk-bot/internal/store"
)

var worldBloomFrameIDs = map[string]int{
	"100": 20001, "tb": 20001,
	"1000": 20002, "tk": 20002,
}

type worldBloomFrameArgs struct {
	Help         bool
	FrameID      int
	CharacterIDs []int
	CardID       int
	Trained      *bool
}

// WorldBloomFrameModule registers and renders the World Bloom custom frame command.
type WorldBloomFrameModule struct {
	md      *masterdata.Loader
	aliases *cards.CharaAliasResolver
	draw    *draw.Client
	binds   *store.Store
	profile *profile.Fetcher
}

func NewWorldBloomFrameModule(md *masterdata.Loader, d *draw.Client, binds *store.Store, fetcher *profile.Fetcher, aliases *cards.CharaAliasResolver) *WorldBloomFrameModule {
	if aliases == nil {
		aliases = cards.NewCharaAliasResolver("")
	}
	return &WorldBloomFrameModule{md: md, aliases: aliases, draw: d, binds: binds, profile: fetcher}
}

func (m *WorldBloomFrameModule) Register(r *router.Router) {
	r.Register("wl边框", nil, m.handle)
}

func isWorldBloomHelpRequest(raw string) bool {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "help", "帮助", "说明", "用法":
		return true
	default:
		return false
	}
}

func parseWorldBloomFrameArgs(raw string, aliases *cards.CharaAliasResolver) (worldBloomFrameArgs, error) {
	var parsed worldBloomFrameArgs
	tokens := strings.Fields(strings.TrimSpace(raw))
	if len(tokens) == 1 {
		switch strings.ToLower(tokens[0]) {
		case "help", "帮助", "说明", "用法":
			parsed.Help = true
			return parsed, nil
		}
	}
	if len(tokens) < 3 {
		return parsed, fmt.Errorf("参数不足：格式为 wl边框 wl3 100/tb <角色缩写> [卡牌ID] [花前/花后]")
	}
	if strings.ToLower(tokens[0]) != "wl3" {
		return parsed, fmt.Errorf("暂不支持该边框类型，目前仅支持 wl3")
	}
	frameID, ok := worldBloomFrameIDs[strings.ToLower(tokens[1])]
	if !ok {
		return parsed, fmt.Errorf("边框颜色仅支持 100/tb 或 1000/tk")
	}

	roleTokens, optional := tokens[2:], []string{}
	if len(roleTokens) >= 6 {
		optional = roleTokens[6:]
		roleTokens = roleTokens[:6]
	} else {
		secondIsNumeric := false
		if len(roleTokens) >= 2 {
			_, secondIsNumeric = parseIntToken(roleTokens[1])
		}
		if len(roleTokens) >= 2 && !secondIsNumeric && aliases != nil && aliases.Resolve(strings.ToLower(roleTokens[1])) > 0 {
			return parsed, fmt.Errorf("角色缩写需要正好提供 1 个或 6 个")
		}
		if len(roleTokens) > 3 {
			return parsed, fmt.Errorf("参数过多：最多再写卡牌ID和花前/花后")
		}
		optional = roleTokens[1:]
		roleTokens = roleTokens[:1]
	}
	if len(optional) > 2 {
		return parsed, fmt.Errorf("角色缩写需要正好提供 1 个或 6 个，后面最多再写卡牌ID和花前/花后")
	}
	if aliases == nil {
		return parsed, fmt.Errorf("角色缩写解析暂不可用")
	}
	for _, alias := range roleTokens {
		characterID := aliases.Resolve(strings.ToLower(alias))
		if characterID < 1 || characterID > 26 {
			return parsed, fmt.Errorf("无法识别角色缩写「%s」，请检查缩写后重试", alias)
		}
		parsed.CharacterIDs = append(parsed.CharacterIDs, characterID)
	}
	if len(parsed.CharacterIDs) == 1 {
		parsed.CharacterIDs = []int{parsed.CharacterIDs[0], parsed.CharacterIDs[0], parsed.CharacterIDs[0], parsed.CharacterIDs[0], parsed.CharacterIDs[0], parsed.CharacterIDs[0]}
	} else if len(parsed.CharacterIDs) != 6 {
		return parsed, fmt.Errorf("角色缩写需要正好提供 1 个或 6 个")
	}

	parsed.FrameID = frameID
	for _, token := range optional {
		switch strings.ToLower(token) {
		case "花前", "normal":
			if parsed.Trained != nil {
				return parsed, fmt.Errorf("花前/花后参数只能指定一次")
			}
			value := false
			parsed.Trained = &value
		case "花后", "after_training":
			if parsed.Trained != nil {
				return parsed, fmt.Errorf("花前/花后参数只能指定一次")
			}
			value := true
			parsed.Trained = &value
		default:
			if parsed.CardID != 0 {
				return parsed, fmt.Errorf("无法识别参数「%s」，卡牌ID后可选花前或花后", token)
			}
			cardID, err := strconv.Atoi(token)
			if err != nil || cardID <= 0 {
				return parsed, fmt.Errorf("卡牌ID必须是大于 0 的数字，花前/花后仅支持这两个选项")
			}
			parsed.CardID = cardID
		}
	}
	return parsed, nil
}

func (m *WorldBloomFrameModule) boundLeaderCard(ctx context.Context, req router.Request) (int, *bool) {
	if m.binds == nil || m.profile == nil {
		return 0, nil
	}
	uid, _, exists, err := m.binds.GetUserBind(ctx, req.Event.UserID, int(req.Server))
	if err != nil || !exists {
		return 0, nil
	}
	p, err := m.profile.GetProfile(ctx, strconv.FormatInt(uid, 10), int(req.Server))
	if err != nil || p == nil || len(p.UserDecks) == 0 || p.UserDecks[0] <= 0 {
		return 0, nil
	}
	trained := len(p.SpecialTraining) > 0 && p.SpecialTraining[0]
	return int(p.UserDecks[0]), &trained
}

func (m *WorldBloomFrameModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if isWorldBloomHelpRequest(req.Arg) {
		// kndbot 的全局 help matcher 负责生成统一帮助图。
		return nil
	}
	parsed, err := parseWorldBloomFrameArgs(req.Arg, m.aliases)
	if err != nil {
		return onebot.ReplyText(req.Event, err.Error(), true)
	}
	if parsed.CardID > 0 {
		if m.md == nil {
			return onebot.ReplyText(req.Event, "读取卡牌数据失败，请稍后重试", false)
		}
		cards, loadErr := m.md.Load("cards.json", int(req.Server))
		if loadErr != nil {
			return onebot.ReplyText(req.Event, "读取卡牌数据失败，请稍后重试", false)
		}
		found := false
		for _, card := range cards {
			if intField(card, "id") == parsed.CardID {
				found = true
				break
			}
		}
		if !found {
			return onebot.ReplyText(req.Event, fmt.Sprintf("没有卡牌 ID %d", parsed.CardID), true)
		}
	}

	payload := map[string]any{"pjsk_type": int(req.Server), "help": parsed.Help}
	if !parsed.Help {
		leaderCardID, leaderTrained := 0, (*bool)(nil)
		if parsed.CardID == 0 {
			leaderCardID, leaderTrained = m.boundLeaderCard(ctx, req)
		}
		payload["frame_id"] = parsed.FrameID
		payload["character_ids"] = parsed.CharacterIDs
		payload["card_id"] = parsed.CardID
		payload["leader_card_id"] = leaderCardID
		payload["leader_trained"] = leaderTrained
		payload["trained"] = parsed.Trained
	}
	imageBytes, err := m.draw.Render(ctx, "world_bloom_frame", payload)
	if err != nil {
		log.Printf("[wl边框] renderer failed: %v", err)
		return onebot.ReplyText(req.Event, "World Bloom 边框或卡牌素材加载失败，请确认资源可用后重试", false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(imageBytes))
}
