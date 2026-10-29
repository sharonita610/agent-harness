import { describe, expect, it } from "vitest";
import { isIdColumn } from "./ResultCard";

describe("isIdColumn", () => {
  it("treats order numbers and ids as identifiers, not quantities", () => {
    for (const c of [
      "id",
      "order_id",
      "run_id",
      "customer_id",
      "주문",
      "번호",
      "주문 번호",
      "이벤트",
      "로트",
      "티켓",
    ]) {
      expect(isIdColumn(c)).toBe(true);
    }
  });
  it("leaves quantities and amounts alone", () => {
    for (const c of [
      "금액",
      "건수",
      "qty",
      "total_amount",
      "재고",
      "안전재고",
      "부족",
      "NG율(%)",
      "정비 경과(일)",
    ]) {
      expect(isIdColumn(c)).toBe(false);
    }
  });
});
