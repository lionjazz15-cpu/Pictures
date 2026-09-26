using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace PencilLine.EditorTools
{
    /// <summary>シェーダー側のアウトライン (背面法線の線) をオフにする</summary>
    public static class OutlineUtil
    {
        /// <summary>アウトラインをオフにしたコピーを dir に作る</summary>
        public static Material CreateNoOutlineCopy(Material src, string dir, out string how)
        {
            var m = new Material(src) { name = src.name + "_NoOutline" };
            how = Disable(m);
            string path = AssetDatabase.GenerateUniqueAssetPath($"{dir}/{m.name}.mat");
            AssetDatabase.CreateAsset(m, path);
            return m;
        }

        /// <summary>マテリアルのアウトラインをオフにして、何をしたかを返す</summary>
        public static string Disable(Material m)
        {
            var done = new List<string>();
            switch (MaterialAdapter.Detect(m))
            {
                case ShaderFamily.MToon0:
                    SetF(m, "_OutlineWidthMode", 0f, done);
                    SetF(m, "_OutlineWidth", 0f, null);
                    m.DisableKeyword("MTOON_OUTLINE_WIDTH_WORLD");
                    m.DisableKeyword("MTOON_OUTLINE_WIDTH_SCREEN");
                    m.DisableKeyword("MTOON_OUTLINE_COLOR_FIXED");
                    m.DisableKeyword("MTOON_OUTLINE_COLOR_MIXED");
                    break;

                case ShaderFamily.MToon10:
                    SetF(m, "_OutlineWidthMode", 0f, done);
                    SetF(m, "_OutlineWidthFactor", 0f, null);
                    m.DisableKeyword("_MTOON_OUTLINE_WORLD");
                    m.DisableKeyword("_MTOON_OUTLINE_SCREEN");
                    break;

                default:
                {
                    // lilToon はアウトライン付きが別シェーダー (例: Hidden/lilToonOutline → lilToon)
                    string name = m.shader != null ? m.shader.name : "";
                    if (name.Contains("Outline"))
                    {
                        Shader alt = FindNoOutlineShader(name);
                        if (alt != null && alt != m.shader)
                        {
                            int q = m.renderQueue;
                            m.shader = alt;
                            m.renderQueue = q;
                            done.Add("シェーダー → " + alt.name);
                        }
                    }
                    // 共通: 太さを 0 に (lilToon / UTS / その他)
                    SetF(m, "_OutlineWidth", 0f, done);
                    SetF(m, "_Outline_Width", 0f, done);
                    SetF(m, "_OutlineWidthFactor", 0f, done);
                    // Poiyomi (ロック前のみ効きます)
                    SetF(m, "_EnableOutlines", 0f, done);
                    break;
                }
            }
            EditorUtility.SetDirty(m);
            return done.Count > 0 ? string.Join(", ", done) : "アウトライン設定なし";
        }

        static Shader FindNoOutlineShader(string name)
        {
            string stripped = name.Replace("Outline", "");
            Shader s = Shader.Find(stripped);
            if (s != null) return s;
            if (stripped == "Hidden/lilToon") return Shader.Find("lilToon");
            return null;
        }

        static void SetF(Material m, string prop, float v, List<string> log)
        {
            if (!m.HasProperty(prop)) return;
            m.SetFloat(prop, v);
            log?.Add(prop + "=" + v);
        }
    }
}
