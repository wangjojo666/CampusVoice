import { ImageResponse } from "next/og";

export const dynamic = "force-static";

export function GET() {
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0e7f6d",
        color: "white",
        fontSize: 195,
        fontWeight: 800,
        padding: 92,
      }}
    >
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 82,
          background: "#0e7f6d",
        }}
      >
        声
      </div>
    </div>,
    { width: 512, height: 512 },
  );
}
