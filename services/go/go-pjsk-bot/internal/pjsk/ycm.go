package pjsk

import (
	"context"
	"encoding/base64"
	"net/http"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"

	"github.com/kazuhira/go-pjsk-bot/internal/draw"
	"github.com/kazuhira/go-pjsk-bot/internal/onebot"
	"github.com/kazuhira/go-pjsk-bot/internal/router"
)

// ycm 推车查询的兜底提示。
const ycmFallback = "出错了，建议直接戳网址：\n" +
	"http://1.117.147.194:8459/\n" +
	"http://59.110.175.37:5000/"

// car 是一条推车信息（对齐 pjsk-draw ycm 渲染器载荷）。
type car struct {
	Room string `json:"room"`
	Des  string `json:"des"`
	Time string `json:"time"`
}

// YcmModule 实现烧烤推车查询：抓取推车站点数据，交 pjsk-draw 出图。
type YcmModule struct {
	draw *draw.Client
	http *http.Client
}

// NewYcmModule 创建 ycm 模块。
func NewYcmModule(d *draw.Client) *YcmModule {
	return &YcmModule{draw: d, http: &http.Client{Timeout: 5 * time.Second}}
}

// Register 注册 ycm 指令。
func (m *YcmModule) Register(r *router.Router) {
	r.Register("ycm", []string{"车来", "有车吗", "推车"}, m.handle)
}

func (m *YcmModule) handle(ctx context.Context, req router.Request) *onebot.ActionRequest {
	if req.Server != router.ServerJP {
		return onebot.ReplyText(req.Event, "抱歉，目前推车查询仅支持日服", true)
	}
	cars := m.getCarsWY(ctx)
	if len(cars) == 0 {
		cars = m.getCarsCC(ctx)
	}
	if len(cars) == 0 {
		return onebot.ReplyText(req.Event, ycmFallback, false)
	}
	payloadCars := make([]map[string]any, 0, len(cars))
	for _, c := range cars {
		payloadCars = append(payloadCars, map[string]any{"room": c.Room, "des": c.Des, "time": c.Time})
	}
	img, err := m.draw.Render(ctx, "ycm", map[string]any{"cars": payloadCars})
	if err != nil {
		return onebot.ReplyText(req.Event, ycmFallback, false)
	}
	return onebot.ReplyImage(req.Event, base64.StdEncoding.EncodeToString(img))
}

// getCarsWY 抓取纹月推车站（div.item 结构）。
func (m *YcmModule) getCarsWY(ctx context.Context) []car {
	doc, err := m.fetchDoc(ctx, "http://1.117.147.194:8459/")
	if err != nil {
		return nil
	}
	var cars []car
	doc.Find("div.item").Each(func(_ int, s *goquery.Selection) {
		room := strings.TrimSpace(s.Find("h3").First().Text())
		var desParts []string
		s.Find("div").Each(func(_ int, d *goquery.Selection) {
			if t := strings.TrimSpace(d.Text()); t != "" {
				desParts = append(desParts, t)
			}
		})
		tm := strings.TrimSpace(s.Find("h5").First().Text())
		cars = append(cars, car{Room: room, Des: strings.Join(desParts, "\n"), Time: tm})
	})
	return cars
}

// getCarsCC 抓取城城推车站（table td，每 4 个一组：跳过/room/des/time）。
func (m *YcmModule) getCarsCC(ctx context.Context) []car {
	doc, err := m.fetchDoc(ctx, "http://59.110.175.37:5000/")
	if err != nil {
		return nil
	}
	var tds []string
	doc.Find("body div table td").Each(func(_ int, s *goquery.Selection) {
		tds = append(tds, strings.TrimSpace(s.Text()))
	})
	return groupCC(tds)
}

// groupCC 把城城站的 td 文本按每 4 个一组解析成推车（跳过/room/des/time）。
func groupCC(tds []string) []car {
	var cars []car
	var cur car
	for i, td := range tds {
		switch i % 4 {
		case 1:
			cur.Room = td
		case 2:
			cur.Des = td
		case 3:
			cur.Time = td
			cars = append(cars, cur)
			cur = car{}
		}
	}
	return cars
}

func (m *YcmModule) fetchDoc(ctx context.Context, url string) (*goquery.Document, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := m.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return goquery.NewDocumentFromReader(resp.Body)
}
