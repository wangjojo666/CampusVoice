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
        fontSize: 73,
        fontWeight: 800,
        padding: 0,
      }}
    >
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 42,
          background: "linear-gradient(145deg, #159b82, #0e675a)",
        }}
      >
        声
      </div>
    </div>,
    { width: 192, height: 192 },
  );
}
