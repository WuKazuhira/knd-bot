package pjsk

import "testing"

func TestGroupCC(t *testing.T) {
	// 每 4 个 td 一组：index%4 == 0 跳过，1=room,2=des,3=time
	tds := []string{
		"skip1", "房间A", "描述A", "时间A",
		"skip2", "房间B", "描述B", "时间B",
	}
	cars := groupCC(tds)
	if len(cars) != 2 {
		t.Fatalf("应解析出 2 辆车, got %d", len(cars))
	}
	if cars[0].Room != "房间A" || cars[0].Des != "描述A" || cars[0].Time != "时间A" {
		t.Errorf("第一辆车解析错误: %+v", cars[0])
	}
	if cars[1].Room != "房间B" || cars[1].Time != "时间B" {
		t.Errorf("第二辆车解析错误: %+v", cars[1])
	}
}

func TestGroupCCIncomplete(t *testing.T) {
	// 不足一组（未到 time）不应产出车
	tds := []string{"skip", "房间", "描述"}
	if cars := groupCC(tds); len(cars) != 0 {
		t.Fatalf("不完整组不应产出车, got %d", len(cars))
	}
}
