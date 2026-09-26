using UnityEditor;
using UnityEngine;

namespace PencilLine.EditorTools
{
    /// <summary>
    /// 1つのマテリアルのパレットを作って細かく調整するウィンドウ
    /// (アバター丸ごとは Avatar Converter で一括変換 → 気になる所だけここで調整)
    /// </summary>
    public class FlatPaletteWindow : EditorWindow
    {
        [MenuItem("Tools/PencilLine/Flat Palette Maker")]
        static void Open()
        {
            GetWindow<FlatPaletteWindow>("Flat Palette");
        }

        /// <summary>元マテリアルとフラット版を指定して開き、すぐ抽出する</summary>
        public static void OpenWith(Material source, Material flat)
        {
            var w = GetWindow<FlatPaletteWindow>("Flat Palette");
            w._source = source;
            w._flat = flat;
            w.RunExtract();
            w.Focus();
        }

        Material _source;   // 元のマテリアル (lilToon / MToon など)
        Material _flat;     // フラット版
        bool _liveUpdate = true;
        Vector2 _scroll;
        readonly PaletteSettings _settings = new PaletteSettings();
        PaletteBuilder _builder;

        PaletteBuilder Builder => _builder ?? (_builder = new PaletteBuilder(_settings));

        void OnGUI()
        {
            EditorGUILayout.LabelField("フラットパレット作成", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox(
                "① 元マテリアルを指定して色を抽出 → ② 必要なら統合先や色を調整 → ③ 保存 → ④ シーンで差し替え。\n" +
                "作成後は色を変えるとリアルタイムで反映されます。アバター丸ごとの変換は Tools > PencilLine > Avatar Converter へ。",
                MessageType.None);

            EditorGUI.BeginChangeCheck();
            var newSource = (Material)EditorGUILayout.ObjectField("元マテリアル", _source, typeof(Material), false);
            if (EditorGUI.EndChangeCheck() && newSource != _source)
            {
                _source = newSource;
                _flat = PaletteBuilder.IsFlat(_source) ? _source : null;
                _builder = null;
            }
            _flat = (Material)EditorGUILayout.ObjectField(new GUIContent("フラット版", "既存のフラット版を指定すると、それを上書き更新します"), _flat, typeof(Material), false);
            if (_flat != null && !PaletteBuilder.IsFlat(_flat)) _flat = null;

            if (_source == null) return;

            DrawSourceInfo();

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("抽出", EditorStyles.boldLabel);
            _settings.clusterCount = EditorGUILayout.IntSlider(new GUIContent("色の数", "模様が消える時は増やす"), _settings.clusterCount, 2, PaletteBuilder.MaxPalette);
            _settings.sampleSize = EditorGUILayout.IntPopup("解析解像度", _settings.sampleSize,
                new[] { "256", "512", "1024" }, new[] { 256, 512, 1024 });

            EditorGUI.BeginChangeCheck();
            _settings.weightL = EditorGUILayout.Slider(new GUIContent("明度の重み", "下げると明暗差を同じ色とみなしやすくなる"), _settings.weightL, 0f, 4f);
            _settings.weightC = EditorGUILayout.Slider(new GUIContent("色みの重み", "上げると色の違いを区別しやすくなる"), _settings.weightC, 0f, 4f);
            if (EditorGUI.EndChangeCheck() && _flat != null)
            {
                Undo.RecordObject(_flat, "Palette Weight");
                _flat.SetFloat("_WeightL", _settings.weightL);
                _flat.SetFloat("_WeightC", _settings.weightC);
            }

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("書き影の自動統合", EditorStyles.boldLabel);
            _settings.hueTolerance = EditorGUILayout.Slider("色相の許容差 (度)", _settings.hueTolerance, 0f, 90f);
            _settings.maxLDiff = EditorGUILayout.Slider("明度差の上限 (有彩色)", _settings.maxLDiff, 0f, 1f);
            _settings.maxLDiffAchromatic = EditorGUILayout.Slider("明度差の上限 (白黒灰)", _settings.maxLDiffAchromatic, 0f, 1f);
            _settings.achromaticChroma = EditorGUILayout.Slider("白黒灰とみなす彩度", _settings.achromaticChroma, 0f, 0.15f);

            EditorGUILayout.Space();
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button("① 色を抽出", GUILayout.Height(26))) RunExtract();
                using (new EditorGUI.DisabledScope(Builder.entries.Count == 0))
                {
                    if (GUILayout.Button("自動統合をやり直す", GUILayout.Height(26)))
                    {
                        Builder.AutoMerge();
                        LiveWrite();
                    }
                }
            }

            DrawEntries();

            EditorGUILayout.Space();
            using (new EditorGUI.DisabledScope(Builder.entries.Count == 0))
            {
                string label = _flat == null ? "③ フラット版マテリアルを作成" : "③ フラット版に保存";
                if (GUILayout.Button(label, GUILayout.Height(26)))
                {
                    _flat = Builder.CreateOrUpdateFlat(_flat);
                }
            }
            using (new EditorGUI.DisabledScope(_flat == null || _flat == _source))
            {
                using (new EditorGUILayout.HorizontalScope())
                {
                    if (GUILayout.Button("④ シーンで 元 → フラット に差し替え")) ReplaceInScene(_source, _flat);
                    if (GUILayout.Button("元に戻す")) ReplaceInScene(_flat, _source);
                }
            }
            _liveUpdate = EditorGUILayout.ToggleLeft("色の変更をリアルタイムで反映", _liveUpdate);
        }

        void RunExtract()
        {
            if (_source == null) return;
            // フラット版があれば、焼き込み結果もその隣に置く (一括変換の出力フォルダと揃える)
            string flatPath = _flat != null ? AssetDatabase.GetAssetPath(_flat) : null;
            Builder.outputDir = !string.IsNullOrEmpty(flatPath) && flatPath.StartsWith("Assets")
                ? System.IO.Path.GetDirectoryName(flatPath).Replace('\\', '/')
                : null;
            if (!Builder.Extract(_source, true, out string error))
            {
                ShowNotification(new GUIContent(error));
                return;
            }
            LiveWrite();
        }

        void LiveWrite()
        {
            if (!_liveUpdate || _flat == null) return;
            if (Builder.LiveWrite(_flat)) SceneView.RepaintAll();
        }

        void DrawSourceInfo()
        {
            var info = MaterialAdapter.Read(_source);
            if (info.family == ShaderFamily.MToon0 || info.family == ShaderFamily.MToon10)
            {
                string ver = info.family == ShaderFamily.MToon0 ? "MToon (VRM 0.x)" : "MToon 1.0 (VRM 1.0)";
                EditorGUILayout.HelpBox(ver + " を検出しました。影色 (Shade Color / Shade Texture) と影の境界位置を引き継げます。", MessageType.Info);
                _settings.useMaterialShade = EditorGUILayout.ToggleLeft("MToon の影色を 1影色 に使う", _settings.useMaterialShade);
            }
            else if (!PaletteBuilder.IsFlat(_source))
            {
                var reasons = new System.Collections.Generic.List<string>();
                if (LilToonBaker.NeedsBake(_source, reasons))
                {
                    EditorGUILayout.HelpBox("lilToon の " + string.Join(" / ", reasons) + " を検出しました。抽出の前にメインテクスチャへ焼き込みます。", MessageType.Info);
                    _settings.bakeLilToonLayers = EditorGUILayout.ToggleLeft("焼き込んでから抽出する", _settings.bakeLilToonLayers);
                    using (new EditorGUI.DisabledScope(!_settings.bakeLilToonLayers))
                    {
                        _settings.bakeScale = EditorGUILayout.IntPopup("焼き込み解像度", _settings.bakeScale,
                            new[] { "メインと同じ", "2倍 (デカールが細かい時)" }, new[] { 1, 2 });
                    }
                }
            }
            if (Builder.BakedTexture != null && Builder.Source == _source)
            {
                using (new EditorGUI.DisabledScope(true))
                {
                    EditorGUILayout.ObjectField("焼き込み結果", Builder.BakedTexture, typeof(Texture2D), false);
                }
            }
            if (Builder.warnings.Count > 0 && Builder.Source == _source)
            {
                EditorGUILayout.HelpBox(string.Join("\n", Builder.warnings), MessageType.Warning);
            }
        }

        void DrawEntries()
        {
            var entries = Builder.entries;
            if (entries.Count == 0 || Builder.Source != _source) return;

            EditorGUILayout.Space();
            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.Label("元の色", EditorStyles.miniBoldLabel, GUILayout.Width(110));
                GUILayout.Label("統合先", EditorStyles.miniBoldLabel, GUILayout.Width(95));
                GUILayout.Label("ベース色", EditorStyles.miniBoldLabel, GUILayout.Width(64));
                GUILayout.Label("1影色", EditorStyles.miniBoldLabel, GUILayout.Width(64));
                GUILayout.Label(new GUIContent("線", "この色の境界に線を引く (PencilLineEffect の『テクスチャ線』)"), EditorStyles.miniBoldLabel, GUILayout.Width(24));
            }

            var options = new string[entries.Count + 1];
            options[0] = "(ベース色)";
            for (int i = 0; i < entries.Count; i++) options[i + 1] = "→ #" + i;

            bool changed = false;
            _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.MinHeight(120), GUILayout.MaxHeight(420));
            for (int i = 0; i < entries.Count; i++)
            {
                var e = entries[i];
                using (new EditorGUILayout.HorizontalScope())
                {
                    Rect sw = GUILayoutUtility.GetRect(24, 18, GUILayout.Width(24));
                    EditorGUI.DrawRect(sw, e.display);
                    GUILayout.Label($"#{i} {e.share * 100f:0.0}%", GUILayout.Width(82));

                    int sel = e.mergeInto + 1;
                    int newSel = EditorGUILayout.Popup(sel, options, GUILayout.Width(95));
                    if (newSel != sel)
                    {
                        Builder.SetMerge(i, newSel - 1);
                        changed = true;
                    }

                    if (e.mergeInto >= 0)
                    {
                        int root = Builder.Root(i);
                        Rect r2 = GUILayoutUtility.GetRect(124, 18, GUILayout.Width(124));
                        EditorGUI.DrawRect(r2, entries[root].baseColor);
                        EditorGUI.BeginChangeCheck();
                        e.lineFlag = EditorGUILayout.Toggle(e.lineFlag, GUILayout.Width(24));
                        if (EditorGUI.EndChangeCheck()) changed = true;
                        GUILayout.Label($"#{root} に統合", EditorStyles.miniLabel);
                    }
                    else
                    {
                        EditorGUI.BeginChangeCheck();
                        e.baseColor = EditorGUILayout.ColorField(GUIContent.none, e.baseColor, true, false, false, GUILayout.Width(60));
                        e.shadowColor = EditorGUILayout.ColorField(GUIContent.none, e.shadowColor, true, false, false, GUILayout.Width(60));
                        e.lineFlag = EditorGUILayout.Toggle(e.lineFlag, GUILayout.Width(24));
                        if (EditorGUI.EndChangeCheck()) changed = true;
                        if (Builder.HasMaterialShade) GUILayout.Label("影色=MToon", EditorStyles.miniLabel);
                        else if (e.shadowFromPaint) GUILayout.Label("影色=書き影", EditorStyles.miniLabel);
                    }
                }
            }
            EditorGUILayout.EndScrollView();

            if (changed) LiveWrite();
        }

        static void ReplaceInScene(Material from, Material to)
        {
            if (from == null || to == null) return;
            int count = 0;
            var all = Object.FindObjectsByType<Renderer>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var r in all)
            {
                var mats = r.sharedMaterials;
                bool hit = false;
                for (int i = 0; i < mats.Length; i++)
                {
                    if (mats[i] == from)
                    {
                        mats[i] = to;
                        hit = true;
                    }
                }
                if (!hit) continue;
                Undo.RecordObject(r, "Replace Material");
                r.sharedMaterials = mats;
                count++;
            }
            Debug.Log($"[PencilLine] {count} 個のRendererで {from.name} → {to.name} に差し替えました");
        }
    }
}
